from __future__ import annotations

from functools import lru_cache

from core.config import settings
from core.retrieve import Hit


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import CrossEncoder

    return CrossEncoder(settings.rerank_model, max_length=512)


def rerank(query: str, hits: list[Hit], k: int | None = None) -> list[Hit]:
    """Cross-encoder rerank. On the cut list: just return hits[:k]."""
    k = k or settings.rerank_k
    if not hits:
        return []
    scores = _model().predict([(query, h.text) for h in hits])
    for h, sc in zip(hits, scores):
        h.score = float(sc)
    return sorted(hits, key=lambda h: -h.score)[:k]
