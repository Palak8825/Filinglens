from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select

from core import answer_cache
from core.answer import NoAPIKey, ask_dict
from core.llm import has_api_key
from core.config import settings
from core.db import Document, Fact, get_session
from core.flags import score
from ingest.parse_pdf import local_path, render_page_png

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
    use_cache: bool = True  # False forces a live answer even for a cached demo question


@app.post("/ask")
def ask_ep(body: AskIn) -> dict:
    try:
        return ask_dict(body.symbol.upper(), body.question, body.doc_type, body.use_cache)
    except NoAPIKey as e:
        raise HTTPException(status_code=503, detail=str(e)) from None


@app.get("/status")
def status() -> dict:
    """Lets the UI explain what works without an API key instead of failing on the first question."""
    return {"has_api_key": has_api_key(), "cached_answers": answer_cache.size(), "model": settings.llm_model}


@app.get("/examples")
def examples(symbol: str | None = None) -> list[dict]:
    """Demo questions with cached answers (clickable in the UI; they work with no API key)."""
    return answer_cache.examples(symbol)


def _png_or_404(path, page: int) -> Response:
    """A cited page whose PDF isn't on disk (e.g. a partial download) is a 404, not a 500: the UI then says
    the image isn't in this download, while the quote itself was verified against the indexed text."""
    try:
        png = render_page_png(path, page)
    except (FileNotFoundError, IndexError, ValueError, RuntimeError):
        raise HTTPException(404) from None
    return Response(png, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


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
    return _png_or_404(d.path, page)


_RAW = (settings.data_dir / "raw").resolve()


@app.get("/evidence/page.png")
def evidence_png(path: str, page: int = 1):
    """Render a page of any filing in the frozen corpus (flag evidence may come from filings that
    aren't in the search index). Only paths inside data/raw are served."""
    p = local_path(path)
    p = (p if p.is_absolute() else Path.cwd() / p).resolve()
    if _RAW not in p.parents or p.suffix.lower() != ".pdf" or not p.exists():
        raise HTTPException(404)
    return _png_or_404(p, page)


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
