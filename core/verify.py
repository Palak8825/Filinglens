"""
Citation verifier. A citation survives only if its quote appears (near-)verbatim in the chunk it
points to. This is the feature: it turns "the model said so" into "page N says so".

Words are matched fuzzily (OCR noise, stray whitespace); figures are matched exactly. Fuzzy matching
alone is blind to digits: a long quote with 1.2% changed to 1.3%, or with last year's revenue put in
place of this year's, still scores ~99 on partial_ratio, so a wrong figure on a real page would have
been marked verified. Found by an external review, confirmed on this code, fixed here.
"""
from __future__ import annotations

import re

from rapidfuzz import fuzz

from core.retrieve import Hit

FUZZ_THRESHOLD = 92  # partial_ratio; tolerates whitespace/OCR noise, rejects paraphrase


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


MIN_FUZZY_LEN = 20  # fuzzy matching only for quotes long enough not to match by accident
MIN_EXACT_LEN = 12  # short quotes (table rows like "NIM 2.6% 2.7% 2.5%") must match exactly...


def _figures_match(q: str, c: str, s: int, e: int) -> bool:
    """The quote's digits must be exactly the digits of the passage it matched, c[s:e].

    Checked against that passage, not the whole chunk: a figure that exists elsewhere on the page
    (last year's column, another line item) must not stand in for the one the quote points at.
    The passage is first widened to whole tokens, so a quote that starts or ends inside a number
    ("6,395" taken from "16,395", "1,410" from "14,100") brings the missing digits in and fails.
    Compared as one digit sequence, so separators and a PDF's stray spaces ("₹72 0 crore") don't
    matter, while any changed, swapped, dropped, truncated or repeated figure does.
    """
    while s > 0 and not c[s - 1].isspace():
        s -= 1
    while e < len(c) and not c[e].isspace():
        e += 1
    return _digits(q) == _digits(c[s:e])


def _span(q: str, c: str) -> tuple[int, int] | None:
    """Where the quote sits in the chunk: exactly if it's a substring, else the best fuzzy alignment."""
    i = c.find(q)
    if i >= 0:
        return i, i + len(q)
    al = fuzz.partial_ratio_alignment(q, c)
    if al is None or al.score < FUZZ_THRESHOLD:
        return None
    return al.dest_start, al.dest_end


def quote_in_chunk(quote: str, chunk_text: str) -> bool:
    q, c = _norm(quote), _norm(chunk_text)
    if len(q) >= MIN_FUZZY_LEN:
        span = _span(q, c)
    # ...and carry both a label and a figure, so "revenue" or a bare "2.6%" still fails.
    elif len(q) >= MIN_EXACT_LEN and re.search(r"[a-z]{2,}", q) and re.search(r"\d", q):
        i = c.find(q)
        span = (i, i + len(q)) if i >= 0 else None
    else:
        return False
    return span is not None and _figures_match(q, c, *span)


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
        kept.append(Citation(ref, quote, h.document_id, h.title, h.doc_type, h.page, True, h.fiscal_period))
    return kept, dropped
