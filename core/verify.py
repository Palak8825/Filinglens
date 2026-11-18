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


def _figures_match(q: str, c: str) -> bool:
    """The quote's digits must be exactly the digits of the passage it fuzzily matched.

    Checked against the aligned span, not the whole chunk: a figure that exists elsewhere on the page
    (last year's column, another line item) must not stand in for the one the quote points at.
    Compared as one digit sequence, so separators and a PDF's stray spaces ("₹72 0 crore") don't
    matter, while any changed, swapped, dropped or repeated figure does.
    """
    al = fuzz.partial_ratio_alignment(q, c)
    if al is None or al.score < FUZZ_THRESHOLD:
        return False
    s, e = al.dest_start, al.dest_end
    while s > 0 and not c[s - 1].isspace():  # widen to whole tokens so a figure isn't cut in half
        s -= 1
    while e < len(c) and not c[e].isspace():
        e += 1
    return _digits(q) in _digits(c[s:e])


def quote_in_chunk(quote: str, chunk_text: str) -> bool:
    q, c = _norm(quote), _norm(chunk_text)
    if len(q) >= MIN_FUZZY_LEN:
        return q in c or _figures_match(q, c)
    # ...and carry both a label and a figure, so "revenue" or a bare "2.6%" still fails.
    if len(q) >= MIN_EXACT_LEN and re.search(r"[a-z]{2,}", q) and re.search(r"\d", q):
        return q in c
    return False


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
