"""
Notable events per stock. Deterministic: no LLM, no network.

Why this works: SEBI LODR Regulation 30 makes listed companies disclose material events as exchange
announcements, so the filing feed already IS the material-events feed. The job here is ranking what is
routine (another independent-director re-appointment) against what is unusual (an NCLT order, an
auditor walking out, a burst of filings in one week while everyone else was quiet).

Inputs: data/raw/manifest.jsonl, every downloaded filing across the 44-stock universe (all doc types are
the baseline; announcements and results-type filings are the candidates). The manifest stores no BSE
category/subcategory, so grouping is by normalised title alone. Note the manifest only holds filings the
downloader kept (results, transcripts, annual reports, shareholding, and announcements whose subject
mentions auditors, resignations or appointments), so a court ruling filed under another subject is not
in this feed at all; it can still appear in the Brief through the results/concall answers.

Score = rarity (IDF of the number of companies that filed a near-identical title)
      + keyword boost (legal / credit / governance / corporate action; also gives the label)
      - routine demotion (known noise; can only push down)
Volume spikes (a symbol's weekly filing count far above its norm while the universe was not) are a
separate "unusual filing activity" item.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from statistics import mean, pstdev

from core.config import settings

MANIFEST = settings.data_dir / "raw" / "manifest.jsonl"
CANDIDATE_TYPES = ("announcement", "results")

# ------------------------------------------------------------------------------------------ normalise
_MONTHS = ("january february march april may june july august september october november december "
           "jan feb mar apr jun jul aug sep sept oct nov dec").split()
STOP = set("""
a an the of and or for with to in on at as by from into via under per upon about be is are was were been has have had
this that these those it its their there here which who whom whose we our us you your i
please find enclosed attached attach herewith hereby kindly refer reference file letter dear sir madam sirs
inform informed informs intimate intimated intimation intimating disclosure disclose disclosed announcement
exchange exchanges bse nse company companies bank limited ltd ltdhas pvt private
regulation regulations sebi lodr listing obligations obligation requirements requirement pursuant read schedule
provision provisions applicable other inter alia iii said thereof therein such same further continuation
submission submitted submit details detail information wise wish note take record update
mr ms mrs shri smt dr sh din frn cin mn no nos dated date day today held effective effect wef w e f
his her him she he they them vide captioned subject sub hon ble honble
quarter quarters year years half nine month months ended ending ends fy financial
""".split()) | set(_MONTHS)

_HONORIFIC = re.compile(r"\b(Mr|Ms|Mrs|Shri|Smt|Dr|Sh|Prof|Justice|Kumari)\b\.?\s*((?:[A-Z][A-Za-z.'\-]*\.?\s*){1,5})")
_MS_FIRM = re.compile(r"\bM/s\.?\s*((?:[A-Z&][\w.&'\-]*\s*){1,6})")
_COMPANY = re.compile(r"\b((?:[A-Z0-9][\w.&'\-]*\s+){1,5})(Limited|Ltd)\b\.?")
_PARENS_ID = re.compile(r"\((?:DIN|FRN|CIN|PAN|UDIN)[^)]*\)", re.I)
_REG = re.compile(r"\bregulations?\s*\d+[\w()]*|securities and exchange board of india|"
                  r"listing obligations? and disclosure requirements?|listing regulations|"
                  r"with reference to the captioned subject|in compliance with|membership no\.?\s*\w+", re.I)


def _stem(t: str) -> str:
    if t.startswith("re") and t[2:] in ("appointment", "appointed", "appoint"):
        t = t[2:]
    for suf, rep in (("ies", "y"), ("sses", "ss")):
        if t.endswith(suf) and len(t) > 5:
            return t[: -len(suf)] + rep
    if t.endswith("s") and not t.endswith("ss") and len(t) > 4:
        t = t[:-1]
    return t


def clean_title(title: str) -> str:
    t = re.sub(r"<br\s*/?>", " ", title or "", flags=re.I).replace("''", "'")
    return " ".join(t.split())


def tokens(title: str, symbol: str | None = None) -> list[str]:
    """Content tokens of a filing title: names, company names, dates, years, months, quarters, numbers,
    amounts, regulation boilerplate and the symbol removed; light stemming. Order kept (for display)."""
    t = clean_title(title)
    t = re.sub(r"Ltd\.?has\b", "Ltd has", t)
    t = _PARENS_ID.sub(" ", t)
    t = _HONORIFIC.sub(" ", t)
    t = _MS_FIRM.sub(" ", t)
    t = _COMPANY.sub(" ", t)
    t = _REG.sub(" ", t)
    t = t.lower()
    t = re.sub(r"\b(re)-(appoint\w*)", r"\1\2", t)
    t = re.sub(r"(rs\.?|inr|₹|usd|\$)\s*[\d,.]+\s*(crores?|cr|lakhs?|mn|million|bn|billion)?", " ", t)
    t = re.sub(r"\bq[1-4]\b|\bh[12]\b|\bfy\s*'?\d*\b|\b\d+(st|nd|rd|th)\b", " ", t)
    t = re.sub(r"[^a-z]+", " ", t)
    out = []
    sym = (symbol or "").lower()
    for w in t.split():
        if len(w) < 3 or w in STOP or w == sym:
            continue
        w = _stem(w)
        if w not in STOP and w not in out:
            out.append(w)
    return out


def signature(title: str, symbol: str | None = None) -> str:
    """Order-free template: "Intimation of Board Meeting" == "Board Meeting Intimation"."""
    return " ".join(sorted(tokens(title, symbol)))


# ------------------------------------------------------------------------------------------ rules
BOOSTS: list[tuple[str, float, re.Pattern[str]]] = [
    ("Legal / regulatory event", 0.7, re.compile(
        r"supreme court|high court|\bnclt\b|\bnclat\b|tribunal|sebi order|\border\b.{0,40}\bsebi\b|penalt|show[- ]cause|"
        r"adjudicat|search|seizure|enforcement directorate|\bcbi\b|\bsfio\b|\binsolvency resolution professional\b|"
        r"\birp\b|\bresolution professional\b|\bcirp\b|litigation|\bsuit\b|arbitra|\bfine\b|\bpetition\b|"
        r"\bwrit\b|\bcourt\b|forensic|\bmoratorium\b|\bmonitoring agency\b|\binvestigation\b", re.I)),
    ("Credit event", 0.6, re.compile(
        r"\bdefault|rating downgrade|downgrad|upgrad|credit rating|\binsolven|\bbankrupt|\bnpa\b|"
        r"\bdelay in (payment|servicing)|non[- ]payment|\brestructur", re.I)),
    ("Auditor exit", 0.6, re.compile(r"resign\w*.{0,60}\bauditor|\bauditor.{0,60}resign|"
                                     r"change in (the )?statutory (central )?auditor|revised audit|"
                                     r"(qualified|adverse) opinion|disclaimer of opinion", re.I)),
    ("Governance: fraud", 0.7, re.compile(r"\bfraud|whistle[- ]?blow|misappropriat|diversion of funds", re.I)),
    ("Top leadership exit", 0.45, re.compile(
        r"resig\w*.{0,80}\b(chairman|chairperson|ceo|cfo|managing director|md|whole[- ]time director|"
        r"chief executive|chief financial|from the board)\b|"
        r"\b(managing director|whole[- ]time director|chairman|chairperson|ceo|cfo|md|chief executive officer|"
        r"chief financial officer)\b.{0,80}\bresig", re.I)),
    ("Leadership change", 0.3, re.compile(
        r"resig\w*.{0,80}\b(director|kmp|key managerial|company secretary|compliance officer|president|chief|head|"
        r"senior management)|"
        r"\b(director|kmp|chief \w+ officer|company secretary|senior management personnel)\b.{0,80}\bresig|"
        r"cessation", re.I)),
    ("Corporate action", 0.5, re.compile(
        r"scheme of (arrangement|amalgamation)|\bmerger|amalgamat|demerg|acquisition|acquire|\bbuy-?back|"
        r"preferential (issue|allotment)|\bqip\b|qualified institutions? placement|rights issue|"
        r"fund ?rais|divest|stake sale|slump sale|conversion of .{0,40}into equity|open offer|delist", re.I)),
    ("Regulator action", 0.4, re.compile(r"\brbi\b.{0,40}(direct|restrict|action|embargo|penal)|"
                                         r"reserve bank of india.{0,60}(direct|restrict|action|embargo|penal)|"
                                         r"section 36ab|supersession", re.I)),
]
ROUTINE: list[re.Pattern[str]] = [re.compile(p, re.I) for p in (
    r"trading window", r"newspaper (publication|advertisement)", r"(loss|duplicate|issue) of share certificate",
    r"(investor|analyst|institutional investor)s? (meet|call|conference)", r"earnings call|conference call",
    r"board meeting (is )?(scheduled|intimation)|meeting of the board .{0,40}scheduled|intimation of board meeting",
    r"compliance certificate", r"registrar and (share )?transfer agent|\brta\b", r"financial results|results? for the quarter",
    r"\btranscript\b", r"audio|video recording", r"investor presentation|press release", r"annual report",
    r"annual general meeting|\bagm\b|postal ballot|e-?voting|scrutini[sz]er", r"secretarial audit",
    r"\bauditors?'? report\b.{0,40}udin|\budin\b", r"shareholding pattern", r"closure of (the )?register|record date",
    r"internal auditor", r"integrated filing", r"regulation 33\b", r"outcome of (the )?board meeting",
    r"quarterly report", r"financial statements", r"members .{0,30}(have )?approved", r"approval of the members",
    r"voting results",
)]
ROUTINE_PENALTY = 0.6


def _boost(text: str) -> tuple[float, str | None]:
    best = (0.0, None)
    for label, w, rx in BOOSTS:
        if rx.search(text) and w > best[0]:
            best = (w, label)
    return best


def _routine(text: str) -> bool:
    return any(rx.search(text) for rx in ROUTINE)


# ------------------------------------------------------------------------------------------ data
@dataclass
class Filing:
    symbol: str
    doc_type: str
    title: str
    filed_on: str
    source_url: str | None
    path: str
    subject: str = ""          # page-1 "Sub:" line, used when the title says nothing ("Please find attached")
    toks: tuple[str, ...] = ()


def _manifest_stamp() -> tuple[int, int]:
    if not MANIFEST.exists():
        return (0, 0)
    st = MANIFEST.stat()
    return (st.st_mtime_ns, st.st_size)


_SUB_RE = re.compile(r"^\s*(sub(ject)?|re)\s*[:.\-–]\s*(.*)$", re.I)


def page1_subject(path: str) -> str:
    """The "Sub:" line of a cover letter (deterministic text extraction, no OCR, no LLM). '' if none."""
    try:
        import pymupdf
        from ingest.parse_pdf import local_path

        with pymupdf.open(local_path(path)) as d:
            text = d[0].get_text("text") if len(d) else ""
    except Exception:  # noqa: BLE001 - missing/corrupt PDF: fall back to the title
        return ""
    lines = [ln.strip() for ln in text.splitlines()]
    for i, ln in enumerate(lines):
        m = _SUB_RE.match(ln)
        if m:
            parts = [m.group(3)]
            j = i + 1
            for j in range(i + 1, min(i + 4, len(lines))):
                nxt = lines[j]
                if not nxt or re.match(r"(dear|ref|respected|sir|madam|with reference|this is|we )", nxt, re.I):
                    break
                parts.append(nxt)
            sub = " ".join(" ".join(parts).split())
            if len(tokens(sub)) < 3:
                # The subject line is itself boilerplate ("Intimation under Regulation 30"): use the opening of
                # the letter's body instead, past the salutation.
                body = " ".join(" ".join(lines[j:j + 25]).split())
                body = re.sub(r"^(dear\s+)?(sir|madam|sirs)[\s/,]*(madam)?[\s,]*", "", body, flags=re.I)
                sub = (sub + " - " + body)[:700]
            return sub[:700]
    return ""


# Cover-letter subjects, committed so the ranking is identical in a download that lacks most PDFs (the demo
# asset). Regenerate after new downloads: python -m core.notable --save-subjects
SUBJECTS_FILE = Path(__file__).resolve().parent.parent / "demo" / "notable_subjects.json"


def _subjects() -> dict[str, str]:
    try:
        return json.loads(SUBJECTS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


@lru_cache(maxsize=2)
def _load(stamp: tuple[int, int]) -> list[Filing]:
    if not MANIFEST.exists():
        return []
    known = _subjects()
    out: list[Filing] = []
    seen: set[str] = set()
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r["path"] in seen:
            continue
        seen.add(r["path"])
        f = Filing(r["symbol"], r["doc_type"], clean_title(r["title"]), r["filed_on"], r.get("source_url"), r["path"])
        toks = tokens(f.title, f.symbol)
        if len(toks) < 3 and f.doc_type in CANDIDATE_TYPES:  # "Please find attached." → read the cover letter
            key = f.path.replace("\\", "/")
            f.subject = known[key] if key in known else page1_subject(f.path)
            if f.subject:
                toks = tokens(f.subject + " " + f.title, f.symbol)
        f.toks = tuple(toks)
        out.append(f)
    return out


def filings() -> list[Filing]:
    return _load(_manifest_stamp())


# ------------------------------------------------------------------------------------------ rarity
def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


NEAR_DUP = 0.5  # token-set Jaccard at which two titles count as the same template


@lru_cache(maxsize=2)
def _rarity_table(stamp: tuple[int, int]) -> tuple[dict[str, int], int]:
    """path → number of distinct companies that filed this template (itself included); universe size.
    A template is shared when another filing's (first 12) content tokens overlap by Jaccard ≥ NEAR_DUP."""
    fs = _load(stamp)
    n_sym = len({f.symbol for f in fs}) or 1
    sets = [(f, frozenset(f.toks[:12])) for f in fs]
    by_tok: dict[str, list[int]] = defaultdict(list)
    for i, (_, s) in enumerate(sets):
        for t in s:
            by_tok[t].append(i)
    out: dict[str, int] = {}
    for i, (f, s) in enumerate(sets):
        if f.doc_type not in CANDIDATE_TYPES:
            continue
        if not s:
            out[f.path] = n_sym  # nothing to say: treat as the most common template
            continue
        cand = {j for t in s for j in by_tok[t]}
        syms = {sets[j][0].symbol for j in cand if _jaccard(s, sets[j][1]) >= NEAR_DUP}
        out[f.path] = len(syms | {f.symbol})
    return out, n_sym


def templates(n: int = 30) -> list[tuple[str, int, int]]:
    """Most common exact signatures across the universe: (signature, filings, companies)."""
    fs = filings()
    cnt: Counter[str] = Counter()
    syms: dict[str, set[str]] = defaultdict(set)
    for f in fs:
        sig = " ".join(sorted(f.toks))
        cnt[sig] += 1
        syms[sig].add(f.symbol)
    return [(s, c, len(syms[s])) for s, c in cnt.most_common(n)]


# ------------------------------------------------------------------------------------------ volume
def _week(d: str) -> date:
    x = date.fromisoformat(d)
    return x - timedelta(days=x.weekday())


VOLUME_MIN = 3  # event filings in one window
WINDOW_WEEKS = 4


@lru_cache(maxsize=2)
def _volume_table(stamp: tuple[int, int]) -> dict[str, dict]:
    """Per symbol, the week whose filing count stands out most against the symbol's own norm, after
    subtracting how unusual that week was for the whole universe (results season lifts everyone)."""
    # Results, transcripts and other scheduled paperwork arrive in bursts every quarter for everyone, so only
    # event filings count: candidates whose title is not on the routine list.
    fs = [f for f in _load(stamp) if f.doc_type in CANDIDATE_TYPES
          and not (_routine(f"{f.title} {f.subject}") and _boost(f"{f.title} {f.subject}")[0] < 0.6)]
    uniq: dict[tuple, Filing] = {}
    for f in fs:  # the same letter filed twice the same day counts once
        uniq.setdefault((f.symbol, f.filed_on, " ".join(f.toks[:10])), f)
    fs = list(uniq.values())
    if not fs:
        return {}
    weeks_all = sorted({_week(f.filed_on) for f in fs})
    first, last = weeks_all[0], weeks_all[-1]
    n_weeks = (last - first).days // 7 + 1
    grid = [first + timedelta(days=7 * i) for i in range(n_weeks)]
    week: dict[str, Counter] = defaultdict(Counter)
    for f in fs:
        week[f.symbol][_week(f.filed_on)] += 1
    syms = list(week)
    # Rolling window of WINDOW_WEEKS weeks, stepped weekly: a run of filings over a fortnight or a month
    # (one crisis unfolding) is caught as well as a single busy week.
    per = {sym: {w: sum(c[w + timedelta(days=7 * k)] for k in range(WINDOW_WEEKS)) for w in grid} for sym, c in week.items()}
    uni_avg = {w: sum(per[s][w] for s in syms) / len(syms) for w in grid}
    um, us = mean(uni_avg.values()), pstdev(uni_avg.values()) or 1.0
    out: dict[str, dict] = {}
    for sym, c in per.items():
        vals = [c[w] for w in grid]
        m, sd = mean(vals), pstdev(vals) or 1.0
        best = None
        for w in grid:
            if c[w] < VOLUME_MIN:
                continue
            excess = (c[w] - m) / sd - (uni_avg[w] - um) / us
            if best is None or excess > best[0]:
                best = (excess, w)
        if best and best[0] >= 3.0:
            excess, w = best
            end = w + timedelta(days=7 * WINDOW_WEEKS - 1)
            inside = [f for f in fs if f.symbol == sym and w <= date.fromisoformat(f.filed_on) <= end]
            lo, hi = min(f.filed_on for f in inside), max(f.filed_on for f in inside)
            out[sym] = {"week_start": lo, "week_end": hi, "weeks": WINDOW_WEEKS,
                        "count": c[w], "typical": round(m, 1), "universe_avg": round(uni_avg[w], 1),
                        "excess_z": round(excess, 1), "titles": [display_title(f) for f in inside][:8]}
    return out


# ------------------------------------------------------------------------------------------ public
@lru_cache(maxsize=4)
def _doc_ids(stamp: tuple[int, int]) -> dict[str, tuple[int, int]]:
    """Normalised path → (document_id, n_pages) for filings that are in the search index."""
    from sqlalchemy import select

    from core.db import Document, get_session

    with get_session() as s:
        rows = s.execute(select(Document.id, Document.path, Document.n_pages)).all()
    return {p.replace("\\", "/").lower(): (i, n or 1) for i, p, n in rows}


def _bse(url: str | None) -> str | None:
    return url.replace("/AttachLive/", "/AttachHis/") if url else url


MIN_SCORE = 0.3  # below this an item is neither rare nor labelled: better to show fewer than pad with noise


def _same_event(a: dict, b: dict) -> bool:
    days = abs((date.fromisoformat(a["filed_on"]) - date.fromisoformat(b["filed_on"])).days)
    if a["_sig"] == b["_sig"] and days <= 31:
        return True
    if days <= 7 and (_jaccard(a["_toks"], b["_toks"]) >= 0.3
                      or (a["label"] and a["label"] == b["label"] and len(a["_toks"] & b["_toks"]) >= 2)):
        return True
    return False


_PREAMBLE = [
    re.compile(r"^outcome of (the )?board( meeting)?( held on [^-–]*)?\s*[-–:]\s*", re.I),
    re.compile(r"^(disclosure|intimation|announcement)\s+(under|pursuant to|in terms of)\s+regulation\s*30.*?"
               r"(2015|lodr\)?)\s*(\([^)]{0,40}\))?\s*([-–:]+\s*|$)", re.I),
    re.compile(r"^(pursuant to|in compliance with|with reference to|this is to inform you that).{0,160}?"
               r"(we wish to inform you that|we would like to inform you that|we hereby inform( you)? that|"
               r"please note that|inform you that)\s*", re.I),
    re.compile(r"^(the company has (informed|intimated) the exchange regarding|we wish to inform about|"
               r"this is to inform you that|please find enclosed( herewith)?( the)?( intimation for)?|"
               r"disclosure under regulation 30\s*[-–:])\s*(the )?", re.I),
]


def display_title(f: Filing) -> str:
    """What to show: the title minus the Regulation 30 preamble; the cover letter's subject when the title
    says nothing ("Please find attached")."""
    t = f.subject if (f.subject and len(tokens(f.title, f.symbol)) < 3) else f.title
    for _ in range(3):
        for rx in _PREAMBLE:
            t = rx.sub("", t).strip(" -–:")
    t = " ".join(re.sub(r"\((?:DIN|FRN|CIN|Membership No)[^)]*\)", "", t, flags=re.I).split())
    t = t[:1].upper() + t[1:]
    return t if len(t) <= 200 else t[:197].rsplit(" ", 1)[0] + "…"


def notable(symbol: str, n: int = 5) -> dict:
    """Top-n notable filings for a symbol, plus an unusual-activity item when its filing volume spiked."""
    symbol = symbol.upper()
    stamp = _manifest_stamp()
    fs = [f for f in filings() if f.symbol == symbol and f.doc_type in CANDIDATE_TYPES]
    rare, n_sym = _rarity_table(stamp)
    ids = _doc_ids(stamp)
    scored = []
    for f in fs:
        k = rare.get(f.path, n_sym)
        rarity = math.log(n_sym / k) / math.log(n_sym) if n_sym > 1 else 0.0
        text = f"{f.title} {f.subject}"
        boost, label = _boost(text)
        routine = _routine(text) and boost < 0.6  # a court/credit/auditor item is never "routine"
        score = rarity + boost - (ROUTINE_PENALTY if routine else 0.0)
        why = []
        if label:
            why.append(label)
        if k <= max(3, n_sym // 10):
            why.append(f"Rare: filed by {k} of {n_sym} companies")
        elif not label:
            why.append(f"Uncommon: filed by {k} of {n_sym} companies")
        doc = ids.get(f.path.replace("\\", "/").lower())
        scored.append({
            "kind": "filing", "title": f.title, "subject": f.subject or None, "filed_on": f.filed_on,
            "doc_type": f.doc_type, "path": f.path, "document_id": doc[0] if doc else None, "page": 1,
            "source_url": _bse(f.source_url), "score": round(score, 3), "rarity_companies": k,
            "label": label, "routine": routine, "why": " · ".join(why),
            "display": display_title(f), "_sig": " ".join(sorted(f.toks[:8])), "_toks": frozenset(f.toks[:12]),
        })
    scored.sort(key=lambda x: (-x["score"], x["filed_on"]))
    picked: list[dict] = []
    for x in scored:
        if x["routine"] or x["score"] < MIN_SCORE:
            continue
        # One event filed several ways (same-day restatements; a resignation and its letter three days
        # later) counts once: near-identical titles within a month, or overlapping ones within a week.
        if any(_same_event(x, p) for p in picked):
            continue
        picked.append(x)
        if len(picked) == n:
            break
    for x in scored:
        x.pop("_sig", None)
        x.pop("_toks", None)
    items = picked
    vol = _volume_table(stamp).get(symbol)
    activity = None
    if vol:
        activity = {"kind": "activity", "title": "Unusual filing activity", "filed_on": vol["week_start"],
                    "date_range": [vol["week_start"], vol["week_end"]], "score": round(0.5 + min(vol["excess_z"], 10) / 20, 3),
                    "why": (f"Unusual filing activity: {vol['count']} event filings between {vol['week_start']} and "
                            f"{vol['week_end']}, vs {vol['typical']:.1f} in a typical {vol['weeks']}-week stretch "
                            f"(other companies averaged {vol['universe_avg']:.1f})"),
                    **{k: vol[k] for k in ("count", "typical", "universe_avg", "excess_z", "titles")}}
    return {"symbol": symbol, "universe_companies": n_sym, "n_filings_considered": len(fs), "items": items,
            "activity": activity,
            "method": "Rarity of the normalised title across all companies' filings, plus keyword labels; "
                      "routine filings pushed down. No AI involved."}


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="YESBANK,IDEA,VEDL,RELIANCE,PAYTM,ADANIENT,GENSOL")
    ap.add_argument("--templates", type=int, default=30)
    ap.add_argument("--save-subjects", action="store_true", help="write demo/notable_subjects.json from the PDFs")
    a = ap.parse_args()
    if a.save_subjects:
        subs = {f.path.replace("\\", "/"): f.subject for f in filings() if f.subject}
        SUBJECTS_FILE.write_text(json.dumps(subs, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
        print(f"{len(subs)} subjects -> {SUBJECTS_FILE}")
    if a.templates:
        print(f"Top {a.templates} normalised templates (filings, companies):")
        for sig, c, k in templates(a.templates):
            print(f"  {c:4d} {k:3d}  {sig[:110]}")
    for sym in a.symbols.split(","):
        r = notable(sym)
        print(f"\n== {sym} ({r['n_filings_considered']} filings)")
        for x in r["items"]:
            print(f"  {x['score']:+.2f} {x['filed_on']} [{x['why']}] {x['display'][:130]}"
                  f"{'  doc=' + str(x['document_id']) if x['document_id'] else ''}")
        if r["activity"]:
            print(f"  ACTIVITY {r['activity']['why']}")
