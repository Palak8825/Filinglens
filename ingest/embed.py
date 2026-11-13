from __future__ import annotations

from functools import lru_cache

import numpy as np

from core.config import settings


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(settings.embed_model)


def embed_texts(texts: list[str], batch_size: int = 32) -> np.ndarray:
    return _model().encode(texts, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=len(texts) > 64)


def embed_query(q: str) -> list[float]:
    return _model().encode([q], normalize_embeddings=True)[0].tolist()
