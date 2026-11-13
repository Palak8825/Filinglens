"""Hybrid retrieval: pgvector cosine + BM25 over the symbol's chunks, fused with RRF."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from rank_bm25 import BM25Okapi
from sqlalchemy import select

from core.config import settings
from core.db import Chunk, Document, get_session
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


def _tok(s: str) -> list[str]:
    return [t for t in "".join(c.lower() if c.isalnum() else " " for c in s).split() if len(t) > 1]


@lru_cache(maxsize=64)
def _bm25_index(symbol: str, doc_type: str | None):
    with get_session() as s:
        q = select(Chunk.id, Chunk.text).join(Document).where(Document.symbol == symbol)
        if doc_type:
            q = q.where(Document.doc_type == doc_type)
        rows = s.execute(q).all()
    ids = [r[0] for r in rows]
    return ids, BM25Okapi([_tok(r[1]) for r in rows])


def _vector_hits(symbol: str, query: str, k: int, doc_type: str | None) -> list[int]:
    qv = embed_query(query)
    with get_session() as s:
        q = (select(Chunk.id).join(Document).where(Document.symbol == symbol)
             .order_by(Chunk.embedding.cosine_distance(qv)).limit(k))
        if doc_type:
            q = q.where(Document.doc_type == doc_type)
        return [r[0] for r in s.execute(q).all()]


def _bm25_hits(symbol: str, query: str, k: int, doc_type: str | None) -> list[int]:
    ids, bm = _bm25_index(symbol, doc_type)
    if not ids:
        return []
    scores = bm.get_scores(_tok(query))
    top = sorted(range(len(ids)), key=lambda i: -scores[i])[:k]
    return [ids[i] for i in top if scores[i] > 0]


def retrieve(symbol: str, query: str, k: int | None = None, doc_type: str | None = None) -> list[Hit]:
    k = k or settings.retrieve_k
    v, b = _vector_hits(symbol, query, k, doc_type), _bm25_hits(symbol, query, k, doc_type)
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
    return [
        Hit(c.id, d.id, d.symbol, d.doc_type, d.fiscal_period, d.title, c.page, c.section, c.text, fused[c.id])
        for c, d in (by_id[i] for i in order if i in by_id)
    ]
