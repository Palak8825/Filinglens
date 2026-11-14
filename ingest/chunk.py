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
_NOT_SPEAKERS = {"ref", "sub", "subject", "date", "dear sir", "note", "source", "website", "email", "encl", "place"}
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
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        j = min(n, i + _CHARS)
        if j < n:  # only back off to a boundary when there is more text to come
            k = max(text.rfind("\n\n", i, j), text.rfind(". ", i, j))
            if k > i + _CHARS // 2:
                j = k + 1
        piece = text[i:j].strip()
        if len(piece) > 80:
            out.append(piece)
        if j >= n:  # tail consumed; without this the overlap step loops a chunk per character
            break
        i = max(j - _OVERLAP, i + 1)
        # Start the next chunk on a word boundary, not mid-word (matters for cited quotes).
        ws = re.search(r"\s", text[i:j])
        if ws:
            i += ws.end()
    return out


def _split_by_speaker(text: str) -> list[tuple[str, str]]:
    """Returns [(speaker, utterance)] for transcript pages."""
    parts, last, last_name = [], 0, None
    for m in SPEAKER_RE.finditer(text):
        if m.group(1).strip().rstrip(".").lower() in _NOT_SPEAKERS:
            continue  # cover-letter labels like "Ref.:" / "Sub.:" — keep them inside the utterance
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
            # Merge adjacent utterances up to the chunk window so a question stays with its
            # answer. Splitting per speaker strands short analyst questions and leaves a
            # retrieved answer with no question attached.
            buf: list[tuple[str, str]] = []
            buf_len = 0

            def flush(buf: list[tuple[str, str]]) -> None:
                nonlocal ordinal
                if not buf:
                    return
                body = "\n".join(f"{sp}: {ut}" for sp, ut in buf)
                lead = max(buf, key=lambda x: len(x[1]))[0]  # the substantive speaker, not the moderator
                for piece in _split_fixed(body) or [body]:
                    out.append(ChunkOut(page_no, f"speaker:{lead}", ordinal, piece))
                    ordinal += 1

            for speaker, utt in _split_by_speaker(text):
                if not utt:
                    continue
                if buf_len + len(utt) > _CHARS and buf:
                    flush(buf)
                    buf, buf_len = buf[-1:], len(buf[-1][1])  # carry one utterance for context
                buf.append((speaker, utt))
                buf_len += len(utt)
            flush(buf)
        else:
            for piece in _split_fixed(text):
                out.append(ChunkOut(page_no, section, ordinal, piece))
                ordinal += 1
    return out
