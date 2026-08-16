#!/usr/bin/env python3
"""Deterministic, dependency-free stub embedder for tests (plan.md §6).

Reads {"id","text"} JSONL on stdin, writes {"id","vector"} JSONL on stdout —
the exact contract `config.embed.cmd` sidecars must speak. The vector is a
normalized, `dim`-length float sequence derived from a sha256 hash of the
text, so identical text always yields an identical vector. It is NOT a real
embedding model: related text is not guaranteed to score close to unrelated
text. It exists so tests can exercise the embed/find pipeline without a
model download.

Usage: python embed_hash.py [--dim N]   (default dim: 32)
"""

from __future__ import annotations

import hashlib
import json
import struct
import sys


def vector_for(text: str, dim: int) -> list[float]:
    out: list[float] = []
    counter = 0
    seed = text.encode("utf-8")
    while len(out) < dim:
        digest = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        for i in range(0, len(digest) - 3, 4):
            if len(out) >= dim:
                break
            (raw,) = struct.unpack(">I", digest[i : i + 4])
            out.append((raw / 0xFFFFFFFF) * 2.0 - 1.0)  # -> [-1, 1)
        counter += 1
    norm = sum(x * x for x in out) ** 0.5
    if norm > 0:
        out = [x / norm for x in out]
    return out


def _parse_dim(argv: list[str]) -> int:
    dim = 32
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--dim" and i + 1 < len(argv):
            dim = int(argv[i + 1])
            i += 2
        elif a.startswith("--dim="):
            dim = int(a.split("=", 1)[1])
            i += 1
        else:
            i += 1
    return dim


def main(argv: list[str]) -> int:
    dim = _parse_dim(argv)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        vec = vector_for(row["text"], dim)
        sys.stdout.write(json.dumps({"id": row["id"], "vector": vec}) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
