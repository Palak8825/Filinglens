"""Hybrid retrieval: flat-numpy cosine + BM25 over the symbol's chunks, fused with RRF."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from rank_bm25 import BM25Okapi
from sqlalchemy import select

from core import vectors
from core.config import settings
from core.db import Chunk, Document, get_session
from core.periods import context_header
from ingest.embed import embed_query


@dataclass
class Hit:
    chunk_id: int
    document_id: int
    symbol: str
    doc_type: str
    fiscal_period: str | None
    title: str
    page: int
    section: str | None
    text: str
    score: float = 0.0


_PERIOD_RE = re.compile(r"\b(q[1-4])\s*-?\s*(fy)\s*'?\s*(\d{2})\b", re.I)


def _tok(s: str) -> list[str]:
    s = _PERIOD_RE.sub(r" \1 \2\3 ", s)  # "Q2FY27" / "Q2 FY 27" / "q2-fy'27" → "q2 fy27"
    return [t for t in "".join(c.lower() if c.isalnum() else " " for c in s).split() if len(t) > 1]


def query_periods(query: str) -> tuple[str, ...] | None:
    """'GNPA in Q3 FY26 vs Q2 FY27?' → ('Q2FY27', 'Q3FY26'). Every quarter named, so cross-quarter
    questions keep all the filings they need. None when no quarter is named."""
    ps = sorted({f"{m.group(1).upper()}FY{m.group(3)}" for m in _PERIOD_RE.finditer(query)})
    return tuple(ps) or None


@lru_cache(maxsize=64)
def _scope(symbol: str, doc_type: str | None, period: tuple[str, ...] | None = None) -> tuple[list[int], BM25Okapi | None]:
    """Chunk ids for this symbol (+doc_type, +fiscal period), plus a BM25 index over header + text."""
    with get_session() as s:
        q = (select(Chunk.id, Chunk.text, Document.symbol, Document.doc_type, Document.fiscal_period, Document.title)
             .join(Document).where(Document.symbol == symbol))
        if doc_type:
            q = q.where(Document.doc_type == doc_type)
        if period:
            q = q.where(Document.fiscal_period.in_(period))
        rows = s.execute(q).all()
    ids = [r[0] for r in rows]
    docs = [_tok(f"{context_header(sym, dt, fp, ti)}\n{t}") for _, t, sym, dt, fp, ti in rows]
    return ids, (BM25Okapi(docs) if ids else None)


def _vector_hits(symbol: str, query: str, k: int, doc_type: str | None, period: tuple[str, ...] | None) -> list[int]:
    ids, _ = _scope(symbol, doc_type, period)
    if not ids:
        return []
    return [cid for cid, _ in vectors.search(embed_query(query), k, allowed_ids=set(ids))]


def _bm25_hits(symbol: str, query: str, k: int, doc_type: str | None, period: tuple[str, ...] | None) -> list[int]:
    ids, bm = _scope(symbol, doc_type, period)
    if bm is None:
        return []
    scores = bm.get_scores(_tok(query))
    top = sorted(range(len(ids)), key=lambda i: -scores[i])[:k]
    return [ids[i] for i in top if scores[i] > 0]


def retrieve(symbol: str, query: str, k: int | None = None, doc_type: str | None = None,
             mode: str = "hybrid") -> list[Hit]:
    """mode: hybrid | bm25 | vector (the latter two exist for the eval ablation)."""
    k = k or settings.retrieve_k
    # A named quarter ("Q2 FY27") is a hard filter: quarters discuss the same topics in the same
    # words, so similarity alone mixes them up. Fall back to all filings if that quarter is empty.
    period = query_periods(query)
    if period and not _scope(symbol, doc_type, period)[0]:
        period = None
    v = _vector_hits(symbol, query, k, doc_type, period) if mode in ("hybrid", "vector") else []
    b = _bm25_hits(symbol, query, k, doc_type, period) if mode in ("hybrid", "bm25") else []
    fused: dict[int, float] = {}
    for lst in (v, b):
        for rank, cid in enumerate(lst):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (60 + rank)
    order = sorted(fused, key=lambda c: -fused[c])[:k]
    if not order:
        return []
    with get_session() as s:
        rows = s.execute(select(Chunk, Document).join(Document).where(Chunk.id.in_(order))).all()
    by_id = {c.id: (c, d) for c, d in rows}
    out: list[Hit] = []
    seen: set[str] = set()
    for c, d in (by_id[i] for i in order if i in by_id):
        key = " ".join(c.text.split())[:300]  # concall context-carry can emit identical chunks
        if key in seen:
            continue
        seen.add(key)
        out.append(Hit(c.id, d.id, d.symbol, d.doc_type, d.fiscal_period, d.title, c.page, c.section, c.text, fused[c.id]))
    return out


def invalidate_scope_cache() -> None:
    """Call after ingesting new documents in the same process."""
    _scope.cache_clear()
