"""
Shareholding pattern + pledge extraction → Fact rows.

Shareholding PDFs are tabular. Strategy: pymupdf text → regex for the two numbers that matter
(promoter %, pledged % of promoter holding). If regex fails, fall back to an LLM extraction with
a strict JSON schema. Either way we record the page so the flag can cite it.
"""
from __future__ import annotations

import json
import re
from datetime import date

from core.db import Fact, get_session
from core.llm import complete_json
from ingest.parse_pdf import parse_pdf

PROMOTER_RE = re.compile(r"promoter(?:\s+and\s+promoter\s+group)?[^\n]{0,80}?(\d{1,2}\.\d{2})\s*%?", re.I)
PLEDGE_RE = re.compile(r"(?:pledged|encumbered)[^\n]{0,120}?(\d{1,2}\.\d{2})\s*%", re.I)

SCHEMA = {
    "promoter_pct": "float, promoter & promoter group holding as % of total equity",
    "pledged_pct_of_promoter": "float, shares pledged/encumbered as % of promoter holding; 0 if none",
    "public_pct": "float",
    "page": "int, page where promoter row appears",
}


def extract_shareholding(pdf_path: str, symbol: str, period: str, as_of: date, document_id: int | None = None) -> Fact:
    pages = parse_pdf(pdf_path)
    payload: dict | None = None
    for page_no, text in pages:
        pm, pl = PROMOTER_RE.search(text), PLEDGE_RE.search(text)
        if pm:
            payload = {
                "promoter_pct": float(pm.group(1)),
                "pledged_pct_of_promoter": float(pl.group(1)) if pl else 0.0,
                "page": page_no,
                "method": "regex",
            }
            break
    if payload is None:
        joined = "\n\n".join(f"[page {p}]\n{t[:3000]}" for p, t in pages[:6])
        payload = complete_json(
            system="Extract shareholding numbers from an Indian listed company's shareholding pattern filing.",
            user=f"Return JSON with keys {json.dumps(SCHEMA)}.\n\n{joined}",
        )
        payload["method"] = "llm"
    fact = Fact(symbol=symbol, kind="shareholding", period=period, as_of=as_of, payload=payload,
                document_id=document_id, page=payload.get("page"))
    with get_session() as s:
        s.add(fact)
        s.commit()
    return fact
