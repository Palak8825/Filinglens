"""
Embeddings. Two backends:

  EMBED_BACKEND=st    (default) sentence-transformers, the real one.
  EMBED_BACKEND=hash  dependency-free hashed random projection. Retrieval quality is poor,
                      but it lets you exercise the whole pipeline before torch is installed.
                      Never use it for the eval numbers or the demo.
"""
from __future__ import annotations

import hashlib
import os
import re
from functools import lru_cache

import numpy as np

from core.config import settings

BACKEND = os.environ.get("EMBED_BACKEND", "st").lower()


@lru_cache(maxsize=1)
def _st_model():
    from sentence_transformers import SentenceTransformer

    try:  # cached copy first: skips the HF Hub round-trip on every load
        return SentenceTransformer(settings.embed_model, local_files_only=True)
    except Exception:  # noqa: BLE001 - first run on a fresh machine: download it
        return SentenceTransformer(settings.embed_model)


_WORD = re.compile(r"[a-z0-9]+")


def _hash_vec(text: str) -> np.ndarray:
    """Deterministic bag-of-words projection into embed_dim. Unit-normalised."""
    v = np.zeros(settings.embed_dim, dtype=np.float32)
    for tok in _WORD.findall(text.lower()):
        h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=8).digest(), "little")
        v[h % settings.embed_dim] += 1.0 if h >> 63 else -1.0
    n = np.linalg.norm(v)
    return v / n if n else v


def embed_texts(texts: list[str], batch_size: int = 32) -> np.ndarray:
    if not texts:
        return np.zeros((0, settings.embed_dim), dtype=np.float32)
    if BACKEND == "hash":
        return np.vstack([_hash_vec(t) for t in texts]).astype(np.float32)
    return _st_model().encode(texts, batch_size=batch_size, normalize_embeddings=True,
                              show_progress_bar=len(texts) > 256).astype(np.float32)


def embed_query(q: str) -> list[float]:
    if BACKEND == "hash":
        return _hash_vec(q).tolist()
    return _st_model().encode([q], normalize_embeddings=True)[0].astype(np.float32).tolist()
