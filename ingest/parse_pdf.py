"""PDF → list of (page_no, text). OCR fallback for scanned pages."""
from __future__ import annotations

from pathlib import Path

import fitz  # pymupdf

MIN_CHARS_FOR_TEXT = 40  # below this we assume scanned page


def _ocr(page: fitz.Page) -> str:
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return ""
    pix = page.get_pixmap(dpi=200)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return pytesseract.image_to_string(img, lang="eng")


def parse_pdf(path: str | Path) -> list[tuple[int, str]]:
    pages: list[tuple[int, str]] = []
    with fitz.open(path) as doc:
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text")
            if len(text.strip()) < MIN_CHARS_FOR_TEXT:
                text = _ocr(page)
            pages.append((i, text))
    return pages


def page_count(path: str | Path) -> int:
    with fitz.open(path) as doc:
        return len(doc)


def render_page_png(path: str | Path, page_no: int, dpi: int = 110) -> bytes:
    """Used by the UI to show the cited page."""
    with fitz.open(path) as doc:
        return doc[page_no - 1].get_pixmap(dpi=dpi).tobytes("png")
