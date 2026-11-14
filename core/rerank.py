from __future__ import annotations

from functools import lru_cache

from core.config import settings
from core.retrieve import Hit

# RERANK=0 (environment or .env) skips the cross-encoder; plain RRF order is used.
RERANK_ENABLED = settings.rerank


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import CrossEncoder

    try:  # cached copy first: no HF Hub round-trip on every load
        return CrossEncoder(settings.rerank_model, max_length=512, local_files_only=True)
    except Exception:  # noqa: BLE001 - first run: download it
        return CrossEncoder(settings.rerank_model, max_length=512)


def rerank(query: str, hits: list[Hit], k: int | None = None, enabled: bool | None = None) -> list[Hit]:
    """Cross-encoder rerank. Falls back to RRF order if disabled or unavailable.
    enabled: None follows the RERANK env var; True/False overrides it (used by the eval ablation)."""
    k = k or settings.rerank_k
    if not hits:
        return []
    if not (RERANK_ENABLED if enabled is None else enabled):
        return hits[:k]
    try:
        scores = _model().predict([(query, h.text) for h in hits])
    except Exception as e:  # noqa: BLE001 - model missing, OOM, download failure
        print(f"[rerank] falling back to RRF order: {e}")
        return hits[:k]
    for h, sc in zip(hits, scores):
        h.score = float(sc)
    return sorted(hits, key=lambda h: -h.score)[:k]
