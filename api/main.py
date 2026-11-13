from __future__ import annotations

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select

from core.answer import ask
from core.db import Document, get_session
from core.flags import score
from ingest.parse_pdf import render_page_png

app = FastAPI(title="FilingLens", version="0.1")


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
    return Response(render_page_png(d.path, page), media_type="image/png")
