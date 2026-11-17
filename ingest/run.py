"""
Manifest → Documents + Chunks in SQLite, embeddings → core/vectors flat index.
Idempotent on document path. Resumable: kill it and re-run.

  python -m ingest.run                          # everything in the manifest
  python -m ingest.run --symbols YESBANK,ZEEL   # only these
  python -m ingest.run --skip-types annual_report
"""
from __future__ import annotations

import argparse
from datetime import date

from sqlalchemy import select
from tqdm import tqdm

from core import vectors
from core.db import Chunk, Document, get_session, init_db
from core.periods import context_header, infer_fiscal_period  # noqa: F401 - re-exported for callers
from ingest.announcements import read_manifest
from ingest.chunk import chunk_pages
from ingest.embed import embed_texts
from ingest.parse_pdf import page_count, parse_pdf


def ingest_all(symbols: set[str] | None = None, skip_types: set[str] | None = None) -> None:
    init_db()
    anns = read_manifest()
    if symbols:
        anns = [a for a in anns if a.symbol.upper() in symbols]
    if skip_types:
        anns = [a for a in anns if a.doc_type not in skip_types]

    with get_session() as s:
        done = {p for (p,) in s.execute(select(Document.path)).all()}
    todo = [a for a in anns if a.path and a.path not in done]
    print(f"{len(anns)} in scope, {len(todo)} to ingest")

    for ann in tqdm(todo, desc="ingest"):
        try:
            pages = parse_pdf(ann.path)
        except Exception as e:  # noqa: BLE001
            print("skip(parse)", ann.path, e)
            continue
        chunks = chunk_pages(pages, ann.doc_type)
        if not chunks:
            continue
        try:
            filed = date.fromisoformat(ann.filed_on)
            period = ann.fiscal_period or infer_fiscal_period(ann.doc_type, filed)
            header = context_header(ann.symbol, ann.doc_type, period, ann.title)
            with get_session() as s:
                doc = Document(symbol=ann.symbol, doc_type=ann.doc_type, fiscal_period=period,
                               filed_on=filed, title=ann.title, path=ann.path,
                               source_url=ann.source_url, n_pages=page_count(ann.path))
                s.add(doc)
                s.flush()
                rows = [Chunk(document_id=doc.id, page=c.page, section=c.section, ordinal=c.ordinal, text=c.text)
                        for c in chunks]
                s.add_all(rows)
                s.flush()
                chunk_ids = [r.id for r in rows]
                s.commit()
            # vectors last: a crash here leaves rows without vectors, which the
            # --reembed pass below repairs. The reverse would orphan vectors.
            vectors.append(chunk_ids, embed_texts([f"{header}\n{c.text}" for c in chunks]))
        except Exception as e:  # noqa: BLE001
            print("skip(ingest)", ann.path, e)
            continue

    print("index:", vectors.stats())


def rechunk(doc_types: set[str]) -> None:
    """Re-parse and re-chunk existing documents of these types in place (after a chunker fix).
    Document ids, paths and page numbers are unchanged, so cached citations and eval gold stay valid;
    only chunk rows and their vectors are replaced. Old vectors are dropped by vectors.compact()."""
    with get_session() as s:
        docs = s.execute(select(Document).where(Document.doc_type.in_(doc_types))).scalars().all()
    before = after = 0
    for d in tqdm(docs, desc="rechunk"):
        try:
            pages = parse_pdf(d.path)
        except Exception as e:  # noqa: BLE001
            print("skip(parse)", d.path, e)
            continue
        chunks = chunk_pages(pages, d.doc_type)
        if not chunks:
            continue
        header = context_header(d.symbol, d.doc_type, d.fiscal_period, d.title)
        with get_session() as s:
            old = s.execute(select(Chunk).where(Chunk.document_id == d.id)).scalars().all()
            before += len(old)
            for c in old:
                s.delete(c)
            s.flush()
            rows = [Chunk(document_id=d.id, page=c.page, section=c.section, ordinal=c.ordinal, text=c.text) for c in chunks]
            s.add_all(rows)
            s.flush()
            ids = [r.id for r in rows]
            s.commit()
        after += len(ids)
        vectors.append(ids, embed_texts([f"{header}\n{c.text}" for c in chunks]))
    with get_session() as s:
        live = {i for (i,) in s.execute(select(Chunk.id)).all()}
    dropped = vectors.compact(live)
    print(f"{len(docs)} documents re-chunked: {before} -> {after} chunks; {dropped} stale vectors removed")
    print("index:", vectors.stats())


def reembed_missing() -> None:
    """Embed any chunk whose id is absent from the vector index (crash repair)."""
    _, ids = vectors.load()
    have = set(ids.tolist())
    with get_session() as s:
        rows = s.execute(select(Chunk.id, Chunk.text, Document.symbol, Document.doc_type,
                                Document.fiscal_period, Document.title).join(Document)).all()
    missing = [(i, f"{context_header(sym, dt, fp, ti)}\n{t}") for i, t, sym, dt, fp, ti in rows if i not in have]
    print(f"{len(missing)} chunks missing vectors")
    for i in tqdm(range(0, len(missing), 256), desc="reembed"):
        batch = missing[i:i + 256]
        vectors.append([b[0] for b in batch], embed_texts([b[1] for b in batch]))
    print("index:", vectors.stats())


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", help="comma-separated; default all")
    p.add_argument("--skip-types", help="comma-separated doc_types to skip, e.g. annual_report")
    p.add_argument("--reembed", action="store_true", help="only fill in missing vectors")
    p.add_argument("--rechunk", help="comma-separated doc_types to re-chunk in place, e.g. concall")
    a = p.parse_args()
    if a.rechunk:
        rechunk({x.strip() for x in a.rechunk.split(",")})
    elif a.reembed:
        reembed_missing()
    else:
        ingest_all(
            symbols={x.strip().upper() for x in a.symbols.split(",")} if a.symbols else None,
            skip_types={x.strip() for x in a.skip_types.split(",")} if a.skip_types else None,
        )
