"""
Flat vector index: two append-only binary files.

  data/processed/vectors.f32   raw float32, row-major, (n, embed_dim), L2-normalised
  data/processed/chunk_ids.i64 raw int64, (n,), parallel to the rows above

Search is a single matmul. At 200k x 384 that's ~300 MB and well under 100 ms on CPU,
which is why this replaces pgvector for a corpus this size. No server, no Docker.
"""
from __future__ import annotations

import threading

import numpy as np

from core.config import settings

_VEC = settings.data_dir / "processed" / "vectors.f32"
_IDS = settings.data_dir / "processed" / "chunk_ids.i64"

_lock = threading.Lock()
_cache: dict[str, object] = {}


def _paths_ready() -> bool:
    return _VEC.exists() and _IDS.exists() and _VEC.stat().st_size > 0


def append(chunk_ids: list[int], vecs: np.ndarray) -> None:
    """Append a batch. vecs must be (len(chunk_ids), embed_dim) and already normalised."""
    if len(chunk_ids) == 0:
        return
    vecs = np.ascontiguousarray(vecs, dtype=np.float32)
    if vecs.shape != (len(chunk_ids), settings.embed_dim):
        raise ValueError(f"expected {(len(chunk_ids), settings.embed_dim)}, got {vecs.shape}")
    _VEC.parent.mkdir(parents=True, exist_ok=True)
    with _VEC.open("ab") as f:
        f.write(vecs.tobytes())
    with _IDS.open("ab") as f:
        f.write(np.asarray(chunk_ids, dtype=np.int64).tobytes())
    with _lock:
        _cache.clear()


def load() -> tuple[np.ndarray, np.ndarray]:
    """(vectors, chunk_ids), memory-mapped and cached until the files change."""
    if not _paths_ready():
        return np.zeros((0, settings.embed_dim), dtype=np.float32), np.zeros((0,), dtype=np.int64)
    stamp = (_VEC.stat().st_mtime_ns, _VEC.stat().st_size)
    with _lock:
        if _cache.get("stamp") != stamp:
            v = np.memmap(_VEC, dtype=np.float32, mode="r").reshape(-1, settings.embed_dim)
            ids = np.fromfile(_IDS, dtype=np.int64)
            n = min(len(v), len(ids))  # tolerate a half-written tail from a killed run
            _cache.update(stamp=stamp, v=v[:n], ids=ids[:n])
        return _cache["v"], _cache["ids"]  # type: ignore[return-value]


def search(query_vec: list[float] | np.ndarray, k: int, allowed_ids: set[int] | None = None) -> list[tuple[int, float]]:
    """Top-k (chunk_id, cosine) pairs, optionally restricted to allowed_ids."""
    v, ids = load()
    if len(ids) == 0:
        return []
    q = np.asarray(query_vec, dtype=np.float32).ravel()
    scores = v @ q
    if allowed_ids is not None:
        if not allowed_ids:
            return []
        mask = np.isin(ids, np.fromiter(allowed_ids, dtype=np.int64, count=len(allowed_ids)))
        scores = np.where(mask, scores, -np.inf)
    k = min(k, len(scores))
    top = np.argpartition(-scores, k - 1)[:k]
    top = top[np.argsort(-scores[top])]
    return [(int(ids[i]), float(scores[i])) for i in top if np.isfinite(scores[i])]


def stats() -> dict:
    v, ids = load()
    return {"n_vectors": int(len(ids)), "dim": settings.embed_dim, "mb": round(v.nbytes / 1e6, 1)}


def reset() -> None:
    for p in (_VEC, _IDS):
        p.unlink(missing_ok=True)
    with _lock:
        _cache.clear()
