"""Brute-force vector search (plan.md §8).

Cosine similarity over float32 BLOBs. Tens of milliseconds under ~50k chunks,
with no ANN index to build, invalidate, or corrupt. `numpy` is imported lazily
inside the scoring function — the tool must keep working without it, which is
why a pure-Python fallback exists and is exercised directly in tests.
"""

from __future__ import annotations

import struct


def blob_to_floats(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def cosine_topk(
    query: list[float], candidates: list[tuple[str, bytes]], k: int
) -> list[tuple[str, float]]:
    """candidates: [(key, float32-LE blob)]. Returns up to k (key, score) pairs,
    highest cosine similarity first. Uses numpy if importable, else falls back
    to a pure-Python scorer — slower, but the tool still works.
    """
    if not candidates or not query:
        return []
    try:
        import numpy as np
    except ImportError:
        return _cosine_topk_py(query, candidates, k)

    lengths = {len(blob) for _, blob in candidates}
    if len(lengths) != 1:
        # Mixed dims shouldn't happen (embed_batch enforces DimMismatch), but
        # a reshape() would crash on it — fall back rather than risk that.
        return _cosine_topk_py(query, candidates, k)
    return _cosine_topk_np(query, candidates, k, np)


def _cosine_topk_np(query, candidates, k, np):
    q = np.asarray(query, dtype=np.float32)
    qn = float(np.linalg.norm(q))
    if qn == 0:
        return []
    mat = np.frombuffer(b"".join(blob for _, blob in candidates), dtype=np.float32)
    mat = mat.reshape(len(candidates), -1)
    norms = np.linalg.norm(mat, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        sims = (mat @ q) / (norms * qn)
    sims = np.nan_to_num(sims, nan=-1.0, posinf=-1.0, neginf=-1.0)
    k = min(k, len(candidates))
    order = np.argsort(-sims)[:k]
    return [(candidates[i][0], float(sims[i])) for i in order]


def _cosine_topk_py(query: list[float], candidates: list[tuple[str, bytes]], k: int) -> list[tuple[str, float]]:
    """Pure-Python fallback — no numpy required. Used when numpy is absent,
    and tested directly to keep the no-numpy path honest."""
    qn = sum(x * x for x in query) ** 0.5
    if qn == 0:
        return []
    scored: list[tuple[str, float]] = []
    for key, blob in candidates:
        vec = blob_to_floats(blob)
        dot = sum(a * b for a, b in zip(query, vec))
        vn = sum(x * x for x in vec) ** 0.5
        sim = (dot / (vn * qn)) if vn > 0 else -1.0
        scored.append((key, sim))
    scored.sort(key=lambda t: -t[1])
    return scored[:k]
