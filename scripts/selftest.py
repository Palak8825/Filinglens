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

    # 3. verifier accepts verbatim, rejects paraphrase and hallucination
    chunk = RESULTS_TEXT
    cases = [
        ("one-time provision of Rs. 42.0 crore relating to a disputed receivable", True, "verbatim"),
        ("EBITDA  margin   declined to 14.2% from 17.8%", True, "verbatim, whitespace noise"),
        ("margins fell because inputs got more expensive this quarter", False, "paraphrase"),
        ("a one-time provision of Rs. 420.0 crore on a disputed receivable", False, "wrong number"),
        ("revenue", False, "too short"),
    ]
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
