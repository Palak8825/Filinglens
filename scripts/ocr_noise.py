"""
Estimate how much indexed text carries OCR noise from the filer's own scanned-PDF text layer.

Why: parse_pdf only treats a page as scanned when it has < 40 characters of text, so a page whose
text layer came from the company's scanner software ("crorc" for "crore") counts as clean text.
Those errors then pass the citation verifier, which checks that a quote is present, not that it is
correct. This script puts a rough number on it for the README's Limitations section.

Heuristic (an estimate, not a measurement): a token is "suspect" if it is alphabetic, at least 5
letters, appears at most twice in the whole corpus, and is one edit (substitute / delete / insert /
swap) from a token that appears at least 300 times. A page is "noisy" with >= 2 suspect tokens.

    python -m scripts.ocr_noise            # summary + examples
"""
from __future__ import annotations

import re
import string
from collections import Counter, defaultdict

from sqlalchemy import select

from core.db import Chunk, Document, get_session

WORD = re.compile(r"[A-Za-z]+")
RARE_MAX, COMMON_MIN, MIN_LEN, PAGE_MIN = 2, 300, 5, 2


def _edits1(w: str) -> set[str]:
    letters = string.ascii_lowercase
    splits = [(w[:i], w[i:]) for i in range(len(w) + 1)]
    return ({a + b[1:] for a, b in splits if b}
            | {a + b[1] + b[0] + b[2:] for a, b in splits if len(b) > 1}
            | {a + c + b[1:] for a, b in splits if b for c in letters}
            | {a + c + b for a, b in splits for c in letters})


def main() -> None:
    with get_session() as s:
        rows = s.execute(select(Chunk.document_id, Chunk.page, Chunk.text, Document.symbol, Document.doc_type)
                         .join(Document)).all()
    freq: Counter[str] = Counter()
    page_tokens: dict[tuple[int, int], list[str]] = defaultdict(list)
    meta: dict[tuple[int, int], tuple[str, str]] = {}
    for doc_id, page, text, sym, dt in rows:
        toks = [t.lower() for t in WORD.findall(text)]
        freq.update(toks)
        page_tokens[(doc_id, page)].extend(toks)
        meta[(doc_id, page)] = (sym, dt)

    common = {w for w, n in freq.items() if n >= COMMON_MIN and len(w) >= MIN_LEN - 1}
    suspect: dict[str, str] = {}
    for w, n in freq.items():
        if n <= RARE_MAX and len(w) >= MIN_LEN:
            near = _edits1(w) & common
            if near:
                suspect[w] = max(near, key=freq.__getitem__)

    noisy = {k: [t for t in set(v) if t in suspect] for k, v in page_tokens.items()}
    noisy = {k: v for k, v in noisy.items() if len(v) >= PAGE_MIN}
    n_pages = len(page_tokens)
    by_type: Counter[str] = Counter(meta[k][1] for k in noisy)
    all_type: Counter[str] = Counter(m[1] for m in meta.values())
    print(f"indexed pages: {n_pages:,} · pages with >= {PAGE_MIN} suspect OCR tokens: {len(noisy):,} "
          f"({len(noisy) / n_pages:.1%})")
    for t, n in by_type.most_common():
        print(f"  {t:<13} {n:>5} of {all_type[t]:>6} pages ({n / all_type[t]:.1%})")
    print("examples (suspect → likely intended):")
    for w in sorted(suspect, key=lambda x: -freq[suspect[x]])[:25]:
        print(f"  {w} → {suspect[w]}")


if __name__ == "__main__":
    main()
