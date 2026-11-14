"""
Manifest → Documents + Chunks in SQLite, embeddings → core/vectors flat index.
Idempotent on document path. Resumable: kill it and re-run.

  python -m ingest.run                          # everything in the manifest
  python -m ingest.run --symbols YESBANK,ZEEL   # only these
  python -m ingest.run --skip-types annual_report
"""
from __future__ import annotations

import argparse
import re
from datetime import date

from sqlalchemy import select
from tqdm import tqdm

from core.db import Chunk, Document, get_session, init_db
from core import vectors
from ingest.announcements import read_manifest
from ingest.chunk import chunk_pages
from ingest.embed import embed_texts
from ingest.parse_pdf import page_count, parse_pdf


QUARTERLY = {"results", "concall", "shareholding"}


def infer_fiscal_period(doc_type: str, filed_on: date) -> str | None:
    """Indian FY ends 31 Mar. Quarterly filings describe the last completed quarter:
    filed Jul-Sep → Q1, Oct-Dec → Q2, Jan-Mar → Q3, Apr-Jun → Q4 of the FY that just ended.
    Annual reports (filed Jun-Sep) describe the FY that ended the previous March."""
    m, y = filed_on.month, filed_on.year
    fy_end = y + 1 if m >= 4 else y  # FY label = calendar year in which that FY ends
    if doc_type in QUARTERLY:
        if 7 <= m <= 9:
            return f"Q1FY{fy_end % 100:02d}"
        if 10 <= m <= 12:
            return f"Q2FY{fy_end % 100:02d}"
        if 1 <= m <= 3:
            return f"Q3FY{fy_end % 100:02d}"
        return f"Q4FY{(fy_end - 1) % 100:02d}"  # Apr-Jun: results for the FY that just closed
    if doc_type == "annual_report":
        return f"FY{(y if m >= 4 else y - 1) % 100:02d}"
    return None


def context_header(symbol: str, doc_type: str, fiscal_period: str | None, title: str) -> str:
    """Prepended to chunk text for embedding/BM25 only (never stored), so retrieval can tell
    Q2 FY27 from Q1 FY27 even when the chunk itself just says "this quarter"."""
    period = re.sub(r"^(Q[1-4])(FY\d{2})$", r"\1 \2", fiscal_period or "")
    return f"{symbol} {doc_type.replace('_', ' ')} {period} | {title[:120]}".strip()


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
    a = p.parse_args()
    if a.reembed:
        reembed_missing()
    else:
        ingest_all(
            symbols={x.strip().upper() for x in a.symbols.split(",")} if a.symbols else None,
            skip_types={x.strip() for x in a.skip_types.split(",")} if a.skip_types else None,
        )
