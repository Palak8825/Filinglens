from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
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


def _png_or_404(path, page: int, hl: str | None = None) -> Response:
    """A cited page whose PDF isn't on disk (e.g. a partial download) is a 404, not a 500: the UI then says
    the image isn't in this download, while the quote itself was verified against the indexed text."""
    try:
        png = render_page_png(path, page, highlight=hl)
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
def page_png(doc_id: int, page: int, hl: str | None = None):
    """hl: a verified quote to highlight on the rendered page."""
    with get_session() as s:
        d = s.get(Document, doc_id)
    if not d:
        raise HTTPException(404)
    return _png_or_404(d.path, page, hl)


_RAW = (settings.data_dir / "raw").resolve()


def _pdf_response(path) -> FileResponse:
    """The filing itself, served inline so the browser's PDF viewer opens it; links add #page=N."""
    p = local_path(path)
    p = (p if p.is_absolute() else Path.cwd() / p).resolve()
    if _RAW not in p.parents or p.suffix.lower() != ".pdf" or not p.exists():
        raise HTTPException(404, "PDF not in this download")
    return FileResponse(p, media_type="application/pdf", filename=p.name, content_disposition_type="inline")


def bse_url(url: str | None) -> str | None:
    """BSE moves attachments from AttachLive to AttachHis after a while, and the Live link then dies; the
    His path serves the same file (checked Nov 2026). Rewritten on the way out; stored data is untouched."""
    return url.replace("/AttachLive/", "/AttachHis/") if url else url


@app.get("/doc/{doc_id}/info")
def doc_info(doc_id: int) -> dict:
    """What a citation points at: title, period, the public BSE URL, and whether the PDF is on disk."""
    with get_session() as s:
        d = s.get(Document, doc_id)
    if not d:
        raise HTTPException(404)
    return {"id": d.id, "symbol": d.symbol, "doc_type": d.doc_type, "title": d.title, "fiscal_period": d.fiscal_period,
            "filed_on": str(d.filed_on) if d.filed_on else None, "n_pages": d.n_pages, "source_url": bse_url(d.source_url),
            "has_pdf": local_path(d.path).exists()}


@app.get("/doc/{doc_id}/pdf")
def doc_pdf(doc_id: int):
    with get_session() as s:
        d = s.get(Document, doc_id)
    if not d:
        raise HTTPException(404)
    return _pdf_response(d.path)


@app.get("/evidence/pdf")
def evidence_pdf(path: str):
    """A flag-evidence filing (may not be in the search index). Only paths inside data/raw are served."""
    return _pdf_response(path)


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


@app.get("/overview")
def overview() -> dict:
    """Headline numbers for the landing page."""
    from sqlalchemy import func

    from core.db import Chunk

    with get_session() as s:
        n_docs = s.execute(select(func.count(Document.id))).scalar() or 0
        n_chunks = s.execute(select(func.count(Chunk.id))).scalar() or 0
        indexed = sorted({r[0] for r in s.execute(select(Document.symbol)).all()})
        by_type = dict(s.execute(select(Document.doc_type, func.count(Document.id)).group_by(Document.doc_type)).all())
        screened = sorted({r[0] for r in s.execute(select(Fact.symbol)).all()})
    manifest = settings.data_dir / "raw" / "manifest.jsonl"
    n_pdfs = sum(1 for line in manifest.open(encoding="utf-8") if line.strip()) if manifest.exists() else None
    return {"filings_indexed": n_docs, "chunks": n_chunks, "symbols_indexed": indexed, "doc_types": by_type,
            "symbols_screened": len(screened), "pdfs_in_corpus": n_pdfs, "cached_answers": answer_cache.size(),
            "has_api_key": has_api_key(), "model": settings.llm_model, "embed_model": settings.embed_model,
            "rerank_model": settings.rerank_model if settings.rerank else None}


@app.get("/shareholding/{symbol}")
def shareholding(symbol: str) -> list[dict]:
    """Quarterly promoter holding and encumbrance (NSE shareholding XBRL), oldest first, for the dashboard chart."""
    keys = ["promoter_pct", "encumbered_pct_of_promoter", "pledged_pct_of_promoter", "public_pct"]
    with get_session() as s:
        rows = s.execute(select(Fact).where(Fact.symbol == symbol.upper(), Fact.kind == "shareholding")
                         .order_by(Fact.as_of)).scalars().all()
    return [{"period": f.period, "as_of": str(f.as_of) if f.as_of else None,
             **{k: f.payload.get(k) for k in keys}} for f in rows if f.payload.get("promoter_pct") is not None]


@app.get("/eval")
def eval_report() -> dict:
    """The committed eval report (eval/report.json) plus the question mix, for the Evaluation page."""
    import json
    from collections import Counter

    root = Path(__file__).resolve().parent.parent / "eval"
    rep = json.loads((root / "report.json").read_text(encoding="utf-8")) if (root / "report.json").exists() else {}
    qs = [json.loads(line) for line in (root / "questions.jsonl").read_text(encoding="utf-8").splitlines()
          if line.strip() and not line.startswith("//")]
    qs = [q for q in qs if q.get("symbol") != "EXAMPLE"]
    def clean(x):  # report.json can hold NaN (a metric over zero questions); JSON responses can't
        if isinstance(x, float) and x != x:
            return None
        if isinstance(x, dict):
            return {k: clean(v) for k, v in x.items()}
        if isinstance(x, list):
            return [clean(v) for v in x]
        return x

    rep = clean(rep)
    return {**rep, "n_questions": len(qs), "question_types": dict(Counter(q["type"] for q in qs)),
            "question_symbols": sorted({q["symbol"] for q in qs})}


@app.get("/notable/{symbol}")
def notable_ep(symbol: str, n: int = 5) -> dict:
    """Notable events from the filing feed: rarity across all companies + keyword labels. No LLM."""
    from core.notable import notable

    return notable(symbol.upper(), n)


@app.get("/brief/{symbol}")
def brief_ep(symbol: str, live: bool = False) -> dict:
    """The Brief. Prepared (cached) briefs work with no API key; live=true builds one now (needs a key, ~4 LLM
    calls). Without either, the rule-based sections come back and the cited sections are marked not prepared."""
    from core.brief import get_brief

    if live and not has_api_key():
        raise HTTPException(status_code=503, detail="No LLM_API_KEY set. Prepared briefs still work.")
    return get_brief(symbol.upper(), live=live)


@app.get("/briefs")
def briefs_ep() -> list[str]:
    """Symbols with a prepared brief."""
    from core.brief import cached_symbols

    return cached_symbols()
