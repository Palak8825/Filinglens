"""
End-to-end smoke test with synthetic documents. No network, no LLM key, no BSE.

  python -m scripts.selftest

Proves: SQLite schema, chunking, embedding, flat vector index, hybrid retrieval,
the citation verifier (accepts verbatim, rejects paraphrase), and the flag engine.
Writes to data/selftest/ so it never touches your real index.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

os.environ.setdefault("DATA_DIR", "./data/selftest")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///./data/selftest/selftest.db")
Path("./data/selftest/processed").mkdir(parents=True, exist_ok=True)

from core import vectors  # noqa: E402
from core.db import Chunk, Document, Fact, get_session, init_db  # noqa: E402
from core.flags import score  # noqa: E402
from core.retrieve import invalidate_scope_cache, retrieve  # noqa: E402
from core.verify import quote_in_chunk  # noqa: E402
from ingest.chunk import chunk_pages  # noqa: E402
from ingest.embed import embed_texts  # noqa: E402

RESULTS_TEXT = """UNAUDITED FINANCIAL RESULTS FOR THE QUARTER ENDED 30 SEPTEMBER 2025
Revenue from operations for the quarter stood at Rs. 1,234.5 crore as against Rs. 1,102.0 crore
in the corresponding quarter of the previous year. EBITDA margin declined to 14.2% from 17.8%,
primarily on account of higher raw material costs and a one-time provision of Rs. 42.0 crore
relating to a disputed receivable. Profit after tax was Rs. 88.1 crore."""

CONCALL_TEXT = """Moderator: We now begin the question-and-answer session.
Analyst: Can you explain the margin compression this quarter?
CFO: Two things. Raw material prices moved against us through July and August, and we took a
one-time provision of 42 crore on a disputed receivable. Excluding that provision, margin would
have been closer to 16.5 percent. We expect margins to normalise by the fourth quarter.
Analyst: And the pledge on promoter holding?
CFO: We have no comment on promoter level matters at this call."""


def _mk(symbol: str, doc_type: str, text: str, title: str) -> int:
    pages = [(1, text)]
    chunks = chunk_pages(pages, doc_type)
    assert chunks, f"chunking produced nothing for {doc_type}"
    with get_session() as s:
        d = Document(symbol=symbol, doc_type=doc_type, fiscal_period="Q2FY26", filed_on=date(2025, 10, 20),
                     title=title, path=f"synthetic://{symbol}/{doc_type}", source_url=None, n_pages=1)
        s.add(d)
        s.flush()
        rows = [Chunk(document_id=d.id, page=c.page, section=c.section, ordinal=c.ordinal, text=c.text) for c in chunks]
        s.add_all(rows)
        s.flush()
        ids = [r.id for r in rows]
        s.commit()
    vectors.append(ids, embed_texts([c.text for c in chunks]))
    return len(ids)


def main() -> int:
    try:  # Windows consoles default to cp1252; print the summary line's dash instead of mojibake
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    fails: list[str] = []
    from core.config import settings
    from core.db import Base, engine

    # Start clean so the test is re-runnable. Refuse to wipe anything but the selftest DB.
    if "selftest" not in settings.database_url or "selftest" not in str(settings.data_dir):
        print(f"refusing to run: not pointed at a selftest DB ({settings.database_url}, {settings.data_dir})")
        return 2
    vectors.reset()
    Base.metadata.drop_all(engine)
    init_db()

    n1 = _mk("TESTCO", "results", RESULTS_TEXT, "Unaudited Financial Results Q2 FY26")
    n2 = _mk("TESTCO", "concall", CONCALL_TEXT, "Transcript of Earnings Call Q2 FY26")
    invalidate_scope_cache()
    total = n1 + n2
    print(f"[{'ok' if total <= 8 else 'FAIL'}] ingested {total} chunks; index={vectors.stats()}")
    # Guards the overlap bug: two short paragraphs must not explode into hundreds of chunks.
    if total > 8:
        fails.append(f"chunk explosion: {total} chunks from 2 short docs (expected <=8)")

    # 1. retrieval finds the margin discussion
    hits = retrieve("TESTCO", "why did EBITDA margin decline this quarter?")
    if not hits:
        fails.append("retrieval returned nothing")
    else:
        print(f"[ok] retrieved {len(hits)} hits; top doc_type={hits[0].doc_type} page={hits[0].page}")
        if not any("one-time provision" in h.text for h in hits[:4]):
            fails.append("top hits miss the provision sentence")

    # 2. speaker splitting on the transcript
    with get_session() as s:
        secs = {c.section for c in s.query(Chunk).all() if c.section}
    if not any(str(x).startswith("speaker:") for x in secs):
        fails.append(f"concall not split by speaker; sections={secs}")
    else:
        print(f"[ok] transcript labelled by speaker: {sorted(x for x in secs if str(x).startswith('speaker:'))}")
    # The analyst's question must travel with the CFO's answer.
    with get_session() as s:
        concall_text = " ".join(c.text for c in s.query(Chunk).all() if str(c.section or "").startswith("speaker:"))
    if "margin compression" not in concall_text:
        fails.append("analyst question dropped from concall chunks")
    else:
        print("[ok] analyst question retained alongside the answer")

    # 2b. a page that starts mid-speech (the previous page's speaker running on) must not be dropped
    pages = [(1, "Moderator: Welcome to the call.\nCFO: Let me walk you through the quarter. Revenue grew well."),
             (2, "and the order book reached a record 5.2 gigawatts, which gives us visibility for two years. "
                 "Margins should recover as the mix normalises.\nAnalyst: Thank you. A follow-up on capex?\nCFO: Capex stays near 700 crore.")]
    cont = [c for c in chunk_pages(pages, "concall") if "record 5.2 gigawatts" in c.text]
    ok = bool(cont) and "CFO: and the order book" in cont[0].text.replace("\n", " ") or \
        bool(cont) and "CFO:" in cont[0].text
    print(f"[{'ok' if ok else 'FAIL'}] transcript text before a page's first speaker is kept and attributed")
    if not ok:
        fails.append("concall continuation text dropped or unattributed")

    # 3. verifier accepts verbatim, rejects paraphrase and hallucination
    chunk = RESULTS_TEXT
    cases = [
        ("one-time provision of Rs. 42.0 crore relating to a disputed receivable", True, "verbatim"),
        ("EBITDA  margin   declined to 14.2% from 17.8%", True, "verbatim, whitespace noise"),
        ("margins fell because inputs got more expensive this quarter", False, "paraphrase"),
        ("a one-time provision of Rs. 420.0 crore on a disputed receivable", False, "wrong number"),
        # Fuzzy matching alone accepted these (partial_ratio ~99): figures must match exactly.
        ("Revenue from operations for the quarter stood at Rs. 1,284.5 crore", False, "long quote, one digit changed"),
        ("Revenue from operations for the quarter stood at Rs. 1,102.0 crore as against", False,
         "last year's figure swapped in (it is elsewhere on the page)"),
        ("higher raw material costs and a one-time provision of Rs. 88.1 crore relating to a disputed receivable",
         False, "another line's figure swapped in"),
        ("EBITDA margin declined to 14.2% from 14.2%", False, "figure reused twice"),
        ("UNAUDITED FINANCIAL RESULTS FOR THE QUARTER ENDED 30 SEPTEMBER 2024", False, "year changed"),
        ("EBlTDA margin decIined to 14.2% from 17.8%, primarily on account", True, "OCR noise in words, figures right"),
        ("revenue", False, "too short"),
        ("14.2%", False, "bare figure"),
    ]
    table = "Return on Equity (annualized) \n8.2% \n8.3% \nNIM \n2.6% \n2.7% \n2.5% \nCost to Income \n64.3%"
    for quote, expect, label in [("NIM \n2.6% \n2.7% \n2.5%", True, "short exact table row"),
                                 ("NIM 2.9% 2.7% 2.5%", False, "short table row, wrong figure")]:
        got = quote_in_chunk(quote, table)
        if got != expect:
            fails.append(f"verifier {label}: expected {expect}, got {got}")
        print(f"[{'ok' if got == expect else 'FAIL'}] verifier {label}: {got}")
    for quote, expect, label in cases:
        got = quote_in_chunk(quote, chunk)
        mark = "ok" if got == expect else "FAIL"
        if got != expect:
            fails.append(f"verifier {label}: expected {expect}, got {got}")
        print(f"[{mark}] verifier {label}: {got}")

    # 4. flag engine on synthetic shareholding facts
    with get_session() as s:
        s.add_all([
            Fact(symbol="TESTCO", kind="shareholding", period="Q3FY25", as_of=date(2024, 12, 31),
                 payload={"promoter_pct": 54.0, "pledged_pct_of_promoter": 8.0, "encumbered_pct_of_promoter": 8.0, "page": 2}, page=2),
            Fact(symbol="TESTCO", kind="shareholding", period="Q2FY26", as_of=date(2025, 9, 30),
                 payload={"promoter_pct": 53.4, "pledged_pct_of_promoter": 31.0, "encumbered_pct_of_promoter": 31.0,
                          "ndu_pct_of_promoter": 0.0, "other_encumbrance_pct_of_promoter": 0.0,
                          "encumbrance_split_available": True, "page": 2}, page=2),
        ])
        s.commit()
        s.add(Fact(symbol="TESTCO", kind="float", period="Q2FY26", as_of=date(2025, 10, 31),
                   payload={"free_float_pct": 12.0, "free_float_period": "Q2FY26", "ret_3m": 0.4, "max_ret_3m": 1.6,
                            "max_window": ["2025-06-01", "2025-09-01"], "price_start": 50.0, "price_end": 130.0}))
        s.add(Fact(symbol="TESTCO", kind="auditor_event", as_of=date(2025, 8, 1), page=1,
                   payload={"event": "resignation", "summary": "Statutory auditor resigned", "filed_on": "2025-08-01"}))
        s.commit()
    sc = score("TESTCO")
    codes = [f["code"] for f in sc["flags"]]
    for want in ("pledge_creep", "float_pump", "auditor_change"):
        if want not in codes:
            fails.append(f"{want} did not fire; flags={codes}")
    if all(w in codes for w in ("pledge_creep", "float_pump", "auditor_change")):
        print(f"[ok] flags fired: {codes} level={sc['level']} score={sc['score']}")
    # And the controls: a clean company must not fire anything.
    with get_session() as s:
        s.add(Fact(symbol="CLEANCO", kind="shareholding", period="Q1FY26", as_of=date(2025, 6, 30),
                   payload={"promoter_pct": 50.0, "encumbered_pct_of_promoter": 0.0}))
        s.add(Fact(symbol="CLEANCO", kind="shareholding", period="Q2FY26", as_of=date(2025, 9, 30),
                   payload={"promoter_pct": 50.1, "encumbered_pct_of_promoter": 0.0}))
        s.commit()
    clean = score("CLEANCO")["flags"]
    if clean:
        fails.append(f"clean company fired flags: {[f['code'] for f in clean]}")
    else:
        print("[ok] clean company fires no flags")

    # 5. relative quarters resolve against what was actually filed (no LLM)
    from core.retrieve import resolve_periods

    for q, want in [("What was the margin in the latest quarter?", ("Q2FY26",)),
                    ("What was the margin in Q1 FY26?", ("Q1FY26",)),
                    ("What was the debt as of September 30, 2025?", ("Q2FY26",)),
                    ("What was the debt on September 15, 2025?", None),
                    ("What is the dividend policy?", None)]:
        got = resolve_periods("TESTCO", q)[0]
        print(f"[{'ok' if got == want else 'FAIL'}] period resolution {q!r} -> {got}")
        if got != want:
            fails.append(f"period resolution {q!r}: expected {want}, got {got}")

    # 5b. period guard: a subject quarter no filing covers gets a boundary answer; ranges/comparisons don't
    from core.retrieve import covered_quarters, uncovered_period

    covered_quarters.cache_clear()
    for q, fires in [("What was the margin in Q1 FY22?", True), ("How has the margin moved since Q1 FY22?", False),
                     ("What was the margin in Q2 FY26?", False), ("What is the dividend policy?", False)]:
        got = uncovered_period("TESTCO", q)
        ok = bool(got) == fires
        print(f"[{'ok' if ok else 'FAIL'}] period guard {q!r} -> {'boundary answer' if got else 'retrieve'}")
        if not ok:
            fails.append(f"period guard {q!r}: expected {'fire' if fires else 'no fire'}")

    # 6. demo answer cache: question-normalised key; model ignored without an API key, honoured with one
    from core import answer_cache

    real_file = answer_cache.CACHE_FILE
    with tempfile.TemporaryDirectory() as tmp:
        answer_cache.CACHE_FILE = Path(tmp) / "answer_cache.json"
        try:
            answer_cache.put("TESTCO", "Why did EBITDA margin decline?", "v1",
                             {"answer": "Raw materials and a provision.", "citations": [], "model": settings.llm_model})
            q = "Why did EBITDA margin decline?"
            checks = [("hit on normalised wording", answer_cache.get("testco", "  why did EBITDA margin decline ", "v1", False), True),
                      ("other prompt version misses", answer_cache.get("TESTCO", q, "v2", False), False)]
            real_model, settings.llm_model = settings.llm_model, "some-other-model"
            checks += [("no key: model ignored", answer_cache.get("TESTCO", q, "v1", False), True),
                       ("with key: other model answers live", answer_cache.get("TESTCO", q, "v1", True), False)]
            settings.llm_model = real_model
            hit = answer_cache.get("TESTCO", q, "v1", False)
            checks.append(("cached badge metadata, index unchanged", hit and hit["cached"] and not hit["cached"]["index_changed"], True))
        finally:
            answer_cache.CACHE_FILE = real_file
    for label, got, want in checks:
        ok = bool(got) == want
        print(f"[{'ok' if ok else 'FAIL'}] cache: {label}")
        if not ok:
            fails.append(f"cache: {label}")

    # 7. eval report prints counts, not percentages, for cells under 5 questions
    from eval.run_eval import fmt

    for cell, want in [({"k": 1, "n": 1}, "1/1"), ({"k": 2, "n": 3}, "2/3"), ({"k": 6, "n": 8}, "75% (6/8)")]:
        got = fmt(cell)
        print(f"[{'ok' if got == want else 'FAIL'}] eval cell {cell} -> {got}")
        if got != want:
            fails.append(f"eval fmt {cell}: expected {want}, got {got}")

    # 8. notable events: title normalisation (no LLM). Word order, dates, numbers, names and boilerplate drop out.
    from core.notable import signature, tokens

    sig_cases = [
        ("Intimation of Board Meeting", "Board Meeting Intimation", True, "word order"),
        ("Board meeting intimation for 12th August, 2025 (Q1 FY26)", "Intimation of Board Meeting on 3 Feb 2024", True,
         "dates, quarters stripped"),
        ("Disclosure under Regulation 30 of SEBI (LODR) Regulations, 2015 - Resignation of Mr. A. B. Sharma as Director",
         "Resignation of Ms. Priya Rao as Director", True, "regulation boilerplate and names stripped"),
        ("Trading Window Closure", "Order of the Hon'ble NCLT", False, "different events differ"),
    ]
    for a, b, same, label in sig_cases:
        got = signature(a) == signature(b)
        print(f"[{'ok' if got == same else 'FAIL'}] notable normalisation, {label}: {signature(a)!r} vs {signature(b)!r}")
        if got != same:
            fails.append(f"notable normalisation {label}")
    leaked = [t for t in tokens("Allotment of 1,25,000 shares at Rs. 450.50 crore on 30.09.2025 in FY26 Q2", "TESTCO")
              if any(ch.isdigit() for ch in t) or t in ("fy", "q2", "september")]
    print(f"[{'ok' if not leaked else 'FAIL'}] notable normalisation strips numbers/amounts/dates: {leaked or 'clean'}")
    if leaked:
        fails.append(f"notable tokens kept numbers/dates: {leaked}")

    # 9. the Brief never shows uncited text: abstained or zero verified citations → "not covered"
    from core.brief import NOT_COVERED, render_section

    brief_cases = [
        ("abstained", {"answer": "Not found in the filings available.", "abstained": True, "citations": []}, False),
        ("answer with no verified citation", {"answer": "Revenue grew 12%.", "abstained": False, "citations": []}, False),
        ("no answer at all", None, False),
        ("cited but opens by saying the filings lack it",
         {"answer": "The provided filings do not contain specific guidance for the quarter.", "abstained": False,
          "citations": [{"document_id": 1, "page": 3, "quote": "forward-looking statements"}]}, False),
        ("answer with a verified citation", {"answer": "Revenue grew 12%.", "abstained": False,
                                             "citations": [{"document_id": 1, "page": 2, "quote": "Revenue grew 12%"}]}, True),
        ("every figure is outside the quotes (model computed it)",
         {"answer": "In Q2 FY27 revenue rose 11.7% year on year.", "abstained": False,
          "citations": [{"document_id": 1, "page": 2, "quote": "TOTAL INCOME 74,525 73,843 66,666"}]}, False),
    ]
    for label, ans, want in brief_cases:
        r = render_section(ans)
        ok = r["covered"] == want and (want or (r["text"] == NOT_COVERED and not r["citations"]))
        print(f"[{'ok' if ok else 'FAIL'}] brief section, {label}: {'shown with citations' if r['covered'] else r['text']}")
        if not ok:
            fails.append(f"brief section {label}")

    # 10. number grounding: a sentence stating a figure that is in no quote is dropped; dates, periods, digits
    #     inside words ("O2C") and units stuck to a number ("4,28,832crs") are handled
    from core.brief import ground_numbers

    g = ground_numbers("On September 30, 2026 (Q2 FY27) total income was ₹74,525 crore. NIM was 8.35%. "
                       "Deposits were ₹4,28,832 crore in the O2C era.",
                       ["TOTAL INCOME 74,525 73,843 66,666", "Deposits ₹ 4,28,832crs 10% YoY"])
    want = "On September 30, 2026 (Q2 FY27) total income was ₹74,525 crore. Deposits were ₹4,28,832 crore in the O2C era."
    print(f"[{'ok' if g == want else 'FAIL'}] brief keeps only sentences whose figures are quoted")
    if g != want:
        fails.append(f"number grounding: {g!r}")

    print()
    if fails:
        print(f"{len(fails)} FAILURE(S):")
        for f in fails:
            print("  -", f)
        return 1
    print("ALL CHECKS PASSED — retrieval, chunking, verifier and flags work end to end.")
    print("Only the LLM answer step is untested here (needs LLM_API_KEY).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
