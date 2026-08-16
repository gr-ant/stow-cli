"""Hashing helpers.

Two hashes do two different jobs (plan.md §5):
  raw_sha   — sha256 of the chunk/section body. Identity, change detection, history.
  embed_sha — sha256 of the exact string handed to the embedder. Cache validity.
"""

from __future__ import annotations

import hashlib


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def short(sha: str, n: int = 8) -> str:
    return sha[:n]
