"""
Section-aware chunking. Page boundaries are preserved so every chunk has one page number
(that's what makes citations verifiable). Concall transcripts are split by speaker.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core.config import settings

# Rough token estimate: 1 token ≈ 4 chars for English financial text.
_CHARS = settings.chunk_tokens * 4
_OVERLAP = settings.chunk_overlap * 4

SPEAKER_RE = re.compile(r"^([A-Z][A-Za-z .'-]{2,40}):\s", re.M)
SECTION_HINTS = [
    (re.compile(r"management discussion", re.I), "MD&A"),
    (re.compile(r"notes to (the )?(financial|accounts)", re.I), "notes"),
    (re.compile(r"related part", re.I), "related_party"),
    (re.compile(r"question[- ]and[- ]answer|q&a session", re.I), "Q&A"),
    (re.compile(r"auditor'?s? report", re.I), "auditor_report"),
    (re.compile(r"segment", re.I), "segment"),
]


@dataclass
class ChunkOut:
    page: int
    section: str | None
    ordinal: int
    text: str


def _detect_section(text: str, prev: str | None) -> str | None:
    head = text[:600]
    for pat, name in SECTION_HINTS:
        if pat.search(head):
            return name
    return prev


def _split_fixed(text: str) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []
    out, i = [], 0
    while i < len(text):
        j = min(len(text), i + _CHARS)
        # back off to a sentence/paragraph boundary
        k = max(text.rfind("\n\n", i, j), text.rfind(". ", i, j))
        if k > i + _CHARS // 2:
            j = k + 1
        out.append(text[i:j].strip())
        i = max(j - _OVERLAP, i + 1)
    return [c for c in out if len(c) > 80]


def _split_by_speaker(text: str) -> list[tuple[str, str]]:
    """Returns [(speaker, utterance)] for transcript pages."""
    parts, last, last_name = [], 0, None
    for m in SPEAKER_RE.finditer(text):
        if last_name is not None:
            parts.append((last_name, text[last:m.start()].strip()))
        last_name, last = m.group(1).strip(), m.end()
    if last_name is not None:
        parts.append((last_name, text[last:].strip()))
    return parts


def chunk_pages(pages: list[tuple[int, str]], doc_type: str) -> list[ChunkOut]:
    out: list[ChunkOut] = []
    ordinal = 0
    section: str | None = None
    for page_no, text in pages:
        section = _detect_section(text, section)
        if doc_type == "concall" and SPEAKER_RE.search(text):
            for speaker, utt in _split_by_speaker(text):
                for piece in _split_fixed(utt):
                    out.append(ChunkOut(page_no, f"speaker:{speaker}", ordinal, f"{speaker}: {piece}"))
                    ordinal += 1
        else:
            for piece in _split_fixed(text):
                out.append(ChunkOut(page_no, section, ordinal, piece))
                ordinal += 1
    return out
