"""Manifest → Documents + Chunks (+ embeddings) in Postgres. Idempotent on document path."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from tqdm import tqdm

from core.db import Chunk, Document, get_session, init_db
from ingest.announcements import read_manifest
from ingest.chunk import chunk_pages
from ingest.embed import embed_texts
from ingest.parse_pdf import page_count, parse_pdf


def ingest_all() -> None:
    init_db()
    anns = read_manifest()
    with get_session() as s:
        existing = {p for (p,) in s.execute(select(Document.path)).all()}
        for ann in tqdm(anns, desc="ingest"):
            if not ann.path or ann.path in existing:
                continue
            try:
                pages = parse_pdf(ann.path)
            except Exception as e:  # noqa: BLE001
                print("skip", ann.path, e)
                continue
            chunks = chunk_pages(pages, ann.doc_type)
            if not chunks:
                continue
            doc = Document(symbol=ann.symbol, doc_type=ann.doc_type, fiscal_period=ann.fiscal_period,
                           filed_on=date.fromisoformat(ann.filed_on), title=ann.title, path=ann.path,
                           source_url=ann.source_url, n_pages=page_count(ann.path))
            s.add(doc)
            s.flush()
            vecs = embed_texts([c.text for c in chunks])
            s.add_all([
                Chunk(document_id=doc.id, page=c.page, section=c.section, ordinal=c.ordinal, text=c.text, embedding=v.tolist())
                for c, v in zip(chunks, vecs)
            ])
            s.commit()


if __name__ == "__main__":
    ingest_all()
