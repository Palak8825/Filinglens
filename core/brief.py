"""
The Brief: a one-page summary of a stock where every line is a verified citation or an explicit
"No quotable answer found."

Sections
  1. Latest quarter at a glance   predetermined question through the normal answer pipeline (sector-aware)
  2. What management said         predetermined question, earnings calls only
  3. Red flags                    core.flags.score (rules, no LLM)
  4. Notable events               core.notable (rules, no LLM); optionally one cited sentence for the top two
                                  items, kept only if a citation verifies against that same filing

Sections 1, 2 and the notable sentences go through core.answer.answer_with_trace unchanged, so retrieval,
period resolution ("latest quarter"), rerank, the citation verifier and the period guard all apply. A section
whose answer abstained or kept zero verified citations is shown as not covered; uncited text is never shown.

Cache: demo/brief_cache.json, keyed by symbol, written only by `python -m scripts.warm_brief`. It stores the
LLM-backed parts; flags and notable events are recomputed on every request (deterministic and cheap).
"""
from __future__ import annotations

import json
import re
import threading
from datetime import date
from functools import lru_cache
from pathlib import Path

from core.config import settings

CACHE_FILE = Path(__file__).resolve().parent.parent / "demo" / "brief_cache.json"
# Owns the limit: "the filings don't cover this" read as if the company hadn't disclosed it, which was false
# (e.g. VEDL's figures were in a table FilingLens couldn't quote). The UI adds the explanation underneath.
NOT_COVERED = "No quotable answer found."
_lock = threading.Lock()

# Banks and lenders get NIM / asset quality / deposits instead of revenue / EBITDA.
LENDERS = {"YESBANK", "HDFCBANK", "INDUSINDBK", "IIFL", "ICICIBANK", "SBIN", "PNB", "RBLBANK", "BANDHANBNK",
           "BAJFINANCE", "SAMMAANCAP"}

Q_LENDER = ("In the latest quarter, what were the net interest margin, the gross NPA ratio, the CASA ratio or "
            "deposit growth, and the net profit, with the year-on-year change where stated?")
# Asks for the press-release headline sentences, not table cells: quotes stitched across table cells are not
# contiguous text, so the verifier (rightly) rejected them and the section showed as not covered.
Q_OTHER = ("What were the headline results for the latest quarter: revenue or total income, EBITDA and net profit, "
           "with their year-on-year change? Use the results press release or highlights where available.")
# Not "guidance" alone: several companies (HDFC Bank among them) say on the call that they don't give guidance,
# so that question found nothing to quote. Outlook, priorities and risks are discussed on every call.
Q_MGMT = ("On the latest quarter earnings call, what did management say about the outlook, their priorities "
          "or the key risks, including any guidance given?")

SECTIONS = [
    {"key": "quarter", "title": "Latest quarter at a glance"},
    {"key": "management", "title": "What management said"},
    {"key": "flags", "title": "Red flags"},
    {"key": "notable", "title": "Notable events"},
]
NOTABLE_SENTENCES = 2
# Prompt v2 (core/answer.py) answers the parts of a multi-metric question the excerpts support and says what
# they don't, where v1 refuses the whole question when one metric is missing. The Brief asks for four metrics
# at once, so v1 left sections empty even when revenue and EBITDA were on the page. v3 adds the table rule
# (quote one row at a time): ADANIENT and GENSOL had correct figures but stitched table quotes that the verifier
# rejected. Citations are verified the same way under all versions.
BRIEF_PROMPT = "v3"


def is_lender(symbol: str) -> bool:
    s = symbol.upper()
    return s in LENDERS or s.endswith("BANK") or s.endswith("BNK")


def questions(symbol: str) -> dict[str, dict]:
    """The predetermined questions, with the doc_type each is restricted to (None = all filings)."""
    return {"quarter": {"question": Q_LENDER if is_lender(symbol) else Q_OTHER, "doc_type": "results"},
            "management": {"question": Q_MGMT, "doc_type": "concall"}}


def notable_question(item: dict) -> str:
    return f"What does the filing of {item['filed_on']} say about: {item.get('display') or item['title']}?"


# Prompt v2 may answer "The provided filings do not contain ..." and cite a passage showing what IS there.
# That is a refusal in substance, so the section reads as not covered rather than as a cited non-answer.
_LEADS_WITH_NO = re.compile(r"^\W*(the\s+)?(provided\s+|available\s+)?(filings?|excerpts?|documents?|transcripts?)\b"
                            r"[^.]{0,80}?\b(do|does|did)\s+not\s+(contain|include|provide|mention|state|specify|disclose)",
                            re.I)


def covered(answer: dict | None) -> bool:
    """A section is shown only when the answer stands, at least one citation verified, and it doesn't open by
    saying the filings lack the answer."""
    return (bool(answer) and not answer.get("abstained") and bool(answer.get("citations"))
            and not _LEADS_WITH_NO.search(answer.get("answer") or ""))


# ------------------------------------------------------------------------------------------ number grounding
# The verifier proves each quote is on its page, but the answer text around the quotes is the model's. Reviewing
# the 14 prepared Briefs found figures that were in no quote: a net interest margin with no source, a change
# the model computed itself (and rounded wrongly), a number "corrected" from an OCR-damaged quote. So: any
# sentence of a Brief answer containing a number that appears in none of its quotes is dropped. A rule, no AI.
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_NOT_FIGURES = re.compile(
    r"\[c\d+(?:\s*,\s*c\d+)*\]"                                   # citation refs
    r"|\b(?:q[1-4]|h[12]|9m)?\s*fy\s*'?\s*\d{2,4}\b|\bq[1-4]\b"    # periods: Q2 FY27, FY28, Q2FY27, Q3
    rf"|\b{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+(?:19|20)\d\d"  # September 30, 2026
    rf"|\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH},?\s+(?:19|20)\d\d"  # 30 September 2026
    r"|\b(?:19|20)\d\d\b"                                          # years
    r"|(?:^|(?<=\s))\d\)",                                         # list markers "1)"
    re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


# Not inside a word like "O2C" or "FY27"; a unit may follow directly ("4,28,832crs", "720cr").
_FIGURE = re.compile(r"(?<![A-Za-z\d])\d[\d,]*(?:\.\d+)?(?!\d)")


def _figures(text: str) -> set[str]:
    out = set()
    for m in _FIGURE.findall(_NOT_FIGURES.sub(" ", text)):
        n = m.replace(",", "").rstrip(",")
        n = n.rstrip("0").rstrip(".") if "." in n else n
        if n and not (n.isdigit() and int(n) < 10):  # single-digit counts ("Tier 1", "3 states") aren't figures
            out.add(n)
    return out


def ground_numbers(text: str, quotes: list[str]) -> str:
    """Drop every sentence that states a figure found in none of the quotes."""
    have = set().union(*(_figures(q) for q in quotes)) if quotes else set()
    kept = [s for s in _SENTENCE.split(text.strip()) if _figures(s) <= have]
    return " ".join(kept)


# Defects in a filing's own PDF text layer, found by checking prepared Briefs against the page images. The quote
# is faithful to the text, so the verifier passes it, but the figure is wrong. Matched on the quote, so the rule
# stops applying if a regenerated answer no longer uses it.
DAMAGED_QUOTES = {
    "ZEEL": ("total income [1(a) to 1(c) +2] 2,472",
             "ZEEL's PDF text reads \"2,472\" for total income where the printed page shows 22,472 (the half-year "
             "total confirms it), so FilingLens doesn't show these figures."),
}


def render_section(answer: dict | None) -> dict:
    """What the UI shows for an LLM-backed section: the cited answer, or the not-covered line. Never uncited text,
    and never a figure that isn't in one of the quotes."""
    bad = DAMAGED_QUOTES.get((answer or {}).get("symbol", ""))
    if bad and any(bad[0] in " ".join(c["quote"].split()).lower() for c in answer.get("citations") or []):
        return {"covered": False, "text": NOT_COVERED, "citations": [], "period_note": None, "note": bad[1]}
    if covered(answer):
        text = ground_numbers(answer["answer"], [c["quote"] for c in answer["citations"]])
        if text:
            return {"covered": True, "text": text, "citations": answer["citations"],
                    "period_note": answer.get("period_note")}
    return {"covered": False, "text": NOT_COVERED, "citations": [], "period_note": None}


# ------------------------------------------------------------------------------------------ build
def _ask(symbol: str, question: str, doc_type: str | None) -> dict:
    from core.answer import answer_with_trace

    ans, _, _ = answer_with_trace(symbol, question, doc_type, prompt=BRIEF_PROMPT)
    return ans.to_dict()


def notable_sentence(symbol: str, item: dict) -> dict | None:
    """One cited sentence for a notable filing. Kept only if a verified citation points at THAT filing."""
    if not item.get("document_id"):
        return None
    ans = _ask(symbol, notable_question(item), item.get("doc_type"))
    cits = [c for c in ans.get("citations", []) if c.get("document_id") == item["document_id"]]
    if ans.get("abstained") or not cits:
        return None
    return {**ans, "citations": cits}


def build_llm_parts(symbol: str, only: set[str] | None = None, base: dict | None = None) -> dict:
    """The parts that need the LLM: two section answers and up to two notable sentences (≤ 4 calls).
    only: rebuild just these parts ("quarter", "management", "notable") on top of `base` (a cached build)."""
    from core.notable import notable

    symbol = symbol.upper()
    base = base or {"sections": {}, "notable_sentences": {}}
    out = {"sections": dict(base["sections"]), "notable_sentences": dict(base["notable_sentences"])}
    for key, q in questions(symbol).items():
        if only is None or key in only:
            out["sections"][key] = _ask(symbol, q["question"], q["doc_type"])
    if only is not None and "notable" not in only:
        return out
    out["notable_sentences"] = {}
    for item in notable(symbol)["items"][:NOTABLE_SENTENCES]:
        s = notable_sentence(symbol, item)
        if s:
            out["notable_sentences"][item["path"]] = s
    return out


def assemble(symbol: str, llm: dict | None, cached: dict | None = None) -> dict:
    """The Brief the API serves. llm=None means no cached brief and no live build (e.g. no API key)."""
    from core.flags import score
    from core.notable import notable

    symbol = symbol.upper()
    qs = questions(symbol)
    nb = notable(symbol)
    sentences = (llm or {}).get("notable_sentences", {})
    for item in nb["items"]:
        s = sentences.get(item["path"])
        item["sentence"] = render_section(s) if s else None
    sections = []
    for sec in SECTIONS:
        row = {**sec}
        if sec["key"] in qs:
            row["question"] = ((llm or {}).get("sections", {}).get(sec["key"]) or {}).get("question") or qs[sec["key"]]["question"]
            if llm is None:
                row.update({"covered": False, "prepared": False, "text": None, "citations": []})
            else:
                row.update({"prepared": True, **render_section(llm["sections"].get(sec["key"]))})
        elif sec["key"] == "flags":
            row.update({"prepared": True, "covered": True, "flags": score(symbol)})
        else:
            row.update({"prepared": True, "covered": bool(nb["items"] or nb["activity"]), "notable": nb})
        sections.append(row)
    return {"symbol": symbol, "lender": is_lender(symbol), "sections": sections, "cached": cached,
            "prepared": llm is not None}


# ------------------------------------------------------------------------------------------ cache
def _read() -> dict:
    if not CACHE_FILE.exists():
        return {"entries": {}}
    return json.loads(CACHE_FILE.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _loaded(stamp: tuple[int, int]) -> dict:
    return _read()


def _data() -> dict:
    if not CACHE_FILE.exists():
        return {"entries": {}}
    st = CACHE_FILE.stat()
    return _loaded((st.st_mtime_ns, st.st_size))


def cache_get(symbol: str) -> dict | None:
    return _data()["entries"].get(symbol.upper())


def cache_put(symbol: str, llm: dict) -> None:
    from core.answer_cache import index_chunks

    with _lock:
        data = _read()
        models = sorted({a.get("model") for a in [*llm["sections"].values(), *llm["notable_sentences"].values()]
                         if a.get("model")})
        data["entries"][symbol.upper()] = {"symbol": symbol.upper(), "generated_on": date.today().isoformat(),
                                           "requested_model": settings.llm_model, "models": models,
                                           "index_chunks": index_chunks(symbol.upper()), "llm": llm}
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(CACHE_FILE)


def cached_symbols() -> list[str]:
    return sorted(_data()["entries"])


def get_brief(symbol: str, live: bool = False) -> dict:
    """Cached brief when present (works with no API key); else a live build when `live` and a key exists;
    else the deterministic sections only, with the LLM sections marked as not prepared."""
    from core.answer_cache import index_chunks
    from core.llm import has_api_key

    symbol = symbol.upper()
    e = cache_get(symbol)
    if e and not live:
        meta = {"generated_on": e["generated_on"], "index_changed": e["index_chunks"] != index_chunks(symbol)}
        return assemble(symbol, e["llm"], cached=meta)
    if live and has_api_key():
        return assemble(symbol, build_llm_parts(symbol))
    return assemble(symbol, None)


def cited_document_ids() -> set[int]:
    """Every document a cached brief cites (for the demo release asset)."""
    ids: set[int] = set()
    for e in _data()["entries"].values():
        for a in [*e["llm"]["sections"].values(), *e["llm"]["notable_sentences"].values()]:
            ids |= {c["document_id"] for c in a.get("citations", []) if c.get("document_id")}
    return ids
