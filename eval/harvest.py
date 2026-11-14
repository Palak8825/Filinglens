"""
Harvest candidate FACTUAL eval questions from indexed text: sentences that state a figure with a
unit next to a trigger phrase ("stood at", "grew by", "basis points", "crore", ...). Each candidate
comes with the document path and page, i.e. the gold citation, ready to paste into questions.jsonl.

Only use this for the factual slice. Reasoning, cross-document, flag and unanswerable questions
must be hand-written — a set lifted verbatim from single chunks is trivially retrievable and
overstates accuracy (say so in the README).

    python -m eval.harvest --symbols YESBANK,INFY --per-doc 3 > eval/candidates.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict

from sqlalchemy import select

from core.db import Chunk, Document, get_session

TRIGGER = re.compile(r"\b(stood at|grew by|grew|increased (by|to)|declined (by|to)|decreased|was at|came in at|"
                     r"improved (by|to)|margin of|guidance|guided|basis points|bps|crore|lakh crore|billion|million|"
                     r"per share|year[- ]on[- ]year|y-o-y|yoy|q-o-q|sequential)\b", re.I)
FIGURE = re.compile(r"(₹|rs\.?|inr|usd|\$)?\s?\d[\d,]*(\.\d+)?\s?(%|percent|crore|cr\b|lakh|bn|billion|million|mn|bps|basis points|x\b)", re.I)
SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def candidates(symbols: list[str], doc_types: list[str], per_doc: int):
    with get_session() as s:
        q = (select(Chunk.text, Chunk.page, Document.symbol, Document.doc_type, Document.fiscal_period,
                    Document.title, Document.path, Document.filed_on)
             .join(Document).where(Document.symbol.in_(symbols), Document.doc_type.in_(doc_types))
             .order_by(Document.filed_on.desc(), Chunk.ordinal))
        rows = s.execute(q).all()
    per = defaultdict(int)
    seen: set[str] = set()
    for text, page, sym, dt, fp, title, path, filed in rows:
        if per[path] >= per_doc:
            continue
        flat = re.sub(r"\s+", " ", text)
        for sent in SENT.split(flat):
            sent = sent.strip()
            if not (60 <= len(sent) <= 320) or not TRIGGER.search(sent) or not FIGURE.search(sent):
                continue
            if sum(c.isdigit() for c in sent) > len(sent) * 0.25:  # table rows, not sentences
                continue
            key = re.sub(r"\W+", "", sent.lower())[:120]
            if key in seen:
                continue
            seen.add(key)
            per[path] += 1
            yield {"symbol": sym, "doc_type": dt, "fiscal_period": fp, "filed_on": str(filed),
                   "title": title[:120], "sentence": sent,
                   "gold": [{"path": path, "page": page}]}
            if per[path] >= per_doc:
                break


def find_pages(symbols: list[str], needles: list[str], periods: list[str] | None = None):
    """Every indexed (document, page) whose text contains ALL needles (whitespace/case-insensitive).
    Use it to make a question's gold list exhaustive: the same figure often appears on several pages
    (press release, deck table, transcript), and a citation to any of them is correct."""
    norm = lambda s: re.sub(r"\s+", " ", s).lower()  # noqa: E731
    want = [norm(n) for n in needles]
    with get_session() as s:
        q = (select(Chunk.text, Chunk.page, Document.path, Document.doc_type, Document.fiscal_period)
             .join(Document).where(Document.symbol.in_(symbols)))
        if periods:
            q = q.where(Document.fiscal_period.in_(periods))
        rows = s.execute(q).all()
    found: dict[tuple[str, int], tuple[str, str | None]] = {}
    for text, page, path, dt, fp in rows:
        if all(w in norm(text) for w in want):
            found[(path, page)] = (dt, fp)
    return [{"path": p, "page": pg, "doc_type": dt, "fiscal_period": fp} for (p, pg), (dt, fp) in sorted(found.items())]


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", required=True)
    p.add_argument("--doc-types", default="concall,results")
    p.add_argument("--per-doc", type=int, default=3)
    p.add_argument("--find", nargs="+", help="list every page containing ALL these strings (for exhaustive gold)")
    p.add_argument("--periods", help="with --find: restrict to fiscal periods, e.g. Q2FY27,Q3FY26")
    a = p.parse_args()
    syms = [s.strip().upper() for s in a.symbols.split(",")]
    if a.find:
        hits = find_pages(syms, a.find, a.periods.split(",") if a.periods else None)
        for h in hits:
            print(f"{h['doc_type']:<13}{h['fiscal_period'] or '-':<8} p{h['page']:<4} {h['path']}", file=sys.stderr)
        print(json.dumps([{"path": h["path"], "page": h["page"]} for h in hits]))
        raise SystemExit(0)
    n = 0
    for c in candidates([s.strip().upper() for s in a.symbols.split(",")], a.doc_types.split(","), a.per_doc):
        sys.stdout.write(json.dumps(c, ensure_ascii=False) + "\n")
        n += 1
    print(f"{n} candidates", file=sys.stderr)
