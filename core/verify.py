"""
Citation verifier. A citation survives only if its quote appears (near-)verbatim in the chunk it
points to. This is the feature: it turns "the model said so" into "page N says so".
"""
from __future__ import annotations

import re

from rapidfuzz import fuzz

from core.retrieve import Hit

FUZZ_THRESHOLD = 92  # partial_ratio; tolerates whitespace/OCR noise, rejects paraphrase


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def quote_in_chunk(quote: str, chunk_text: str) -> bool:
    q, c = _norm(quote), _norm(chunk_text)
    if len(q) < 20:
        return False
    if q in c:
        return True
    return fuzz.partial_ratio(q, c) >= FUZZ_THRESHOLD


def verify_citations(raw_citations: list[dict], ref_map: dict[str, Hit]):
    from core.answer import Citation  # local import to avoid cycle

    kept: list[Citation] = []
    dropped = 0
    for c in raw_citations:
        ref, quote = str(c.get("ref", "")), str(c.get("quote", ""))
        h = ref_map.get(ref)
        if h is None:
            dropped += 1
            continue
        ok = quote_in_chunk(quote, h.text)
        if not ok:
            dropped += 1
            continue
        kept.append(Citation(ref, quote, h.document_id, h.title, h.doc_type, h.page, True))
    return kept, dropped
