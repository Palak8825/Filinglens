from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select

from core.answer import ask
from core.config import settings
from core.db import Document, Fact, get_session
from core.flags import score
from ingest.parse_pdf import render_page_png

app = FastAPI(title="FilingLens", version="0.1")


@app.on_event("startup")
def _warm() -> None:
    """Load the embedding model and per-symbol BM25 indexes in the background, so the first
    question of a demo is as fast as the rest (cold model load is ~10-30 s on CPU)."""
    import threading

    def run() -> None:
        from core.retrieve import _scope
        from ingest.embed import embed_query

        embed_query("warm up")
        with get_session() as s:
            for (sym,) in s.execute(select(Document.symbol).distinct()).all():
                _scope(sym, None, None)
        print("[warm] embedding model and BM25 indexes ready")

    threading.Thread(target=run, daemon=True).start()


class AskIn(BaseModel):
    symbol: str
    question: str
    doc_type: str | None = None


@app.post("/ask")
def ask_ep(body: AskIn) -> dict:
    return ask(body.symbol.upper(), body.question, body.doc_type).to_dict()


@app.get("/flags/{symbol}")
def flags_ep(symbol: str) -> dict:
    return score(symbol.upper())


@app.get("/symbols")
def symbols() -> list[str]:
    with get_session() as s:
        return sorted({r[0] for r in s.execute(select(Document.symbol)).all()})


@app.get("/docs/{symbol}")
def docs(symbol: str) -> list[dict]:
    with get_session() as s:
        rows = s.execute(select(Document).where(Document.symbol == symbol.upper()).order_by(Document.filed_on.desc())).scalars()
        return [{"id": d.id, "doc_type": d.doc_type, "title": d.title, "filed_on": str(d.filed_on), "n_pages": d.n_pages} for d in rows]


@app.get("/doc/{doc_id}/page/{page}.png")
def page_png(doc_id: int, page: int):
    with get_session() as s:
        d = s.get(Document, doc_id)
    if not d:
        raise HTTPException(404)
    return Response(render_page_png(d.path, page), media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


_RAW = (settings.data_dir / "raw").resolve()


@app.get("/evidence/page.png")
def evidence_png(path: str, page: int = 1):
    """Render a page of any filing in the frozen corpus (flag evidence may come from filings that
    aren't in the search index). Only paths inside data/raw are served."""
    p = Path(path)
    p = (p if p.is_absolute() else Path.cwd() / p).resolve()
    if _RAW not in p.parents or p.suffix.lower() != ".pdf" or not p.exists():
        raise HTTPException(404)
    try:
        png = render_page_png(p, page)
    except (IndexError, ValueError):
        raise HTTPException(404) from None
    return Response(png, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/screen")
def screen() -> list[dict]:
    """Risk score for every symbol that has facts, highest first."""
    with get_session() as s:
        syms = sorted({r[0] for r in s.execute(select(Fact.symbol)).all()})
    rows = []
    for sym in syms:
        r = score(sym)
        rows.append({"symbol": sym, "level": r["level"], "score": r["score"],
                     "flags": [f["code"] for f in r["flags"]]})
    return sorted(rows, key=lambda r: (-r["score"], r["symbol"]))
