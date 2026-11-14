"""PDF → list of (page_no, text). OCR fallback for scanned pages."""
from __future__ import annotations

from pathlib import Path

import fitz  # pymupdf

MIN_CHARS_FOR_TEXT = 40  # below this we assume scanned page


_ocr_warned = False


def _ocr(page: fitz.Page) -> str:
    """OCR one page. Never raises: a missing Tesseract binary or a bad page yields "" so the
    rest of the document still ingests (otherwise one scanned signature page drops the whole PDF)."""
    global _ocr_warned
    try:
        import pytesseract
        from PIL import Image

        pix = page.get_pixmap(dpi=200)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        return pytesseract.image_to_string(img, lang="eng")
    except Exception as e:  # noqa: BLE001 - incl. TesseractNotFoundError
        if not _ocr_warned:
            print(f"[parse_pdf] OCR unavailable, scanned pages will be empty: {type(e).__name__}: {e}")
            _ocr_warned = True
        return ""


def local_path(path: str | Path) -> Path:
    """Paths are stored as written at ingest time (Windows-style 'data\\raw\\...' here). Normalise the
    separators so a shipped index opens the same PDFs on macOS/Linux."""
    return Path(str(path).replace("\\", "/"))


def parse_pdf(path: str | Path) -> list[tuple[int, str]]:
    pages: list[tuple[int, str]] = []
    with fitz.open(local_path(path)) as doc:
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text")
            if len(text.strip()) < MIN_CHARS_FOR_TEXT:
                text = _ocr(page)
            pages.append((i, text))
    return pages


def page_count(path: str | Path) -> int:
    with fitz.open(local_path(path)) as doc:
        return len(doc)


def render_page_png(path: str | Path, page_no: int, dpi: int = 110) -> bytes:
    """Used by the UI to show the cited page."""
    with fitz.open(local_path(path)) as doc:
        return doc[page_no - 1].get_pixmap(dpi=dpi).tobytes("png")
