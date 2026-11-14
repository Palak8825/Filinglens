from __future__ import annotations

import os
from functools import lru_cache

from core.config import settings
from core.retrieve import Hit

# RERANK=0 skips the 1.1 GB cross-encoder entirely (no download, RRF order is used).
RERANK_ENABLED = os.environ.get("RERANK", "1").lower() not in {"0", "false", ""}


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import CrossEncoder

    return CrossEncoder(settings.rerank_model, max_length=512)


def rerank(query: str, hits: list[Hit], k: int | None = None) -> list[Hit]:
    """Cross-encoder rerank. Falls back to RRF order if disabled or unavailable."""
    k = k or settings.rerank_k
    if not hits:
        return []
    if not RERANK_ENABLED:
        return hits[:k]
    try:
        scores = _model().predict([(query, h.text) for h in hits])
    except Exception as e:  # noqa: BLE001 - model missing, OOM, download failure
        print(f"[rerank] falling back to RRF order: {e}")
        return hits[:k]
    for h, sc in zip(hits, scores):
        h.score = float(sc)
    return sorted(hits, key=lambda h: -h.score)[:k]
