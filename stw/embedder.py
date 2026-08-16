"""Embedder sidecar (plan.md §6).

stw never loads a model. It shells JSONL to `config.embed.cmd` and reads
JSONL back, matched on id. Vectors are stored as float32 little-endian BLOBs
via `struct` so writing them never requires numpy.

Batches commit independently (`with tx(conn)` per batch): an interrupted
`stw embed` or `stw find` backfill leaves durable progress and a retry just
picks up the chunks still marked dirty.
"""

from __future__ import annotations

import json
import struct
import subprocess

from .chunker import embed_input
from .db import tx
from .errors import DimMismatch, EmbedFailed, NoEmbedder


def vector_to_blob(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def blob_to_vector(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def _run_sidecar(cmd: list[str], items: list[tuple[str, str]]) -> dict[str, list[float]]:
    """items: [(id, text)]. Returns {id: vector}.

    Raises RuntimeError (plain, caller wraps it) on a nonzero exit, a
    malformed output line, or a missing id — every case names the sidecar's
    stderr so `stw embed` can quote it.
    """
    payload = "\n".join(json.dumps({"id": i, "text": t}) for i, t in items) + "\n"
    try:
        proc = subprocess.run(cmd, input=payload, capture_output=True, text=True, timeout=300)
    except FileNotFoundError as e:
        raise RuntimeError(f"sidecar not found ({e})") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError("sidecar timed out after 300s") from None

    stderr = (proc.stderr or "").strip()[:800]
    if proc.returncode != 0:
        raise RuntimeError(f"sidecar exited {proc.returncode}. stderr: {stderr}")

    result: dict[str, list[float]] = {}
    for lineno, line in enumerate(proc.stdout.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"malformed output line {lineno} ({e}). stderr: {stderr}") from None
        if not isinstance(row, dict) or "id" not in row or "vector" not in row:
            raise RuntimeError(f"output line {lineno} missing id/vector. stderr: {stderr}")
        result[row["id"]] = row["vector"]

    missing = [i for i, _ in items if i not in result]
    if missing:
        raise RuntimeError(
            f"sidecar returned {len(result)} of {len(items)} ids "
            f"(missing {len(missing)}). stderr: {stderr}"
        )
    return result


def embed_batch(ws, conn, rows, *, prefix_doc: str, model: str, dim: int) -> int:
    """Embed one batch of chunk rows (sqlite3.Row with chunk_id/embed_sha/
    heading_path/text) and commit. Returns the number of chunk rows covered.

    Raises NoEmbedder, DimMismatch, or RuntimeError (wrapped into EmbedFailed
    by embed_dirty, which knows the workspace-wide pending count).
    """
    cmd = ws.config["embed"].get("cmd") or []
    if not cmd:
        raise NoEmbedder()

    # De-dup by embed_sha: identical heading+text (e.g. two chunks that are
    # byte-identical after a copy) only needs one sidecar round trip.
    by_sha: dict[str, str] = {}
    for r in rows:
        if r["embed_sha"] not in by_sha:
            by_sha[r["embed_sha"]] = embed_input(r["heading_path"], r["text"], prefix_doc)
    items = list(by_sha.items())

    vectors = _run_sidecar(cmd, items)
    for esha, vec in vectors.items():
        if len(vec) != dim:
            raise DimMismatch(dim, len(vec))

    with tx(conn):
        for esha, vec in vectors.items():
            conn.execute(
                "INSERT INTO embeddings(embed_sha, model, dim, vec) VALUES(?,?,?,?) "
                "ON CONFLICT(embed_sha) DO UPDATE SET "
                "model=excluded.model, dim=excluded.dim, vec=excluded.vec",
                (esha, model, dim, vector_to_blob(vec)),
            )
        placeholders = ",".join("?" for _ in vectors)
        conn.execute(
            f"UPDATE chunks SET dirty = 0 WHERE embed_sha IN ({placeholders})",
            list(vectors.keys()),
        )
    return len(rows)


def embed_dirty(ws, conn, *, limit: int | None = None, batch_size: int | None = None) -> dict:
    """Embed dirty chunks, oldest-dirty-first, one independently-committed
    batch at a time. Returns {"embedded", "batches", "remaining"}.

    On failure, batches already committed stay committed — that's the whole
    point of per-batch commits (plan.md §6 "find must never stall").
    """
    cfg = ws.config["embed"]
    batch_size = batch_size or max(1, int(cfg.get("batch", 64)))
    prefix_doc = cfg.get("prefix_doc", "")
    model = cfg.get("model", "")
    dim = int(cfg.get("dim", 384))

    total_dirty = conn.execute("SELECT COUNT(*) n FROM chunks WHERE dirty = 1").fetchone()["n"]
    to_process = total_dirty if limit is None else min(total_dirty, limit)
    total_batches = -(-to_process // batch_size) if to_process else 0  # ceil div

    embedded = 0
    batches = 0
    while limit is None or embedded < limit:
        take = batch_size if limit is None else min(batch_size, limit - embedded)
        rows = conn.execute(
            "SELECT rowid, chunk_id, embed_sha, heading_path, text FROM chunks "
            "WHERE dirty = 1 ORDER BY rowid ASC LIMIT ?",
            (take,),
        ).fetchall()
        if not rows:
            break
        try:
            embed_batch(ws, conn, rows, prefix_doc=prefix_doc, model=model, dim=dim)
        except RuntimeError as e:
            pending = conn.execute("SELECT COUNT(*) n FROM chunks WHERE dirty = 1").fetchone()["n"]
            where = f"{batches + 1}/{total_batches}" if total_batches else str(batches + 1)
            raise EmbedFailed(f"sidecar failed on batch {where}: {e}", pending) from None
        embedded += len(rows)
        batches += 1

    remaining = conn.execute("SELECT COUNT(*) n FROM chunks WHERE dirty = 1").fetchone()["n"]
    return {"embedded": embedded, "batches": batches, "remaining": remaining}


def embed_query(ws, text: str) -> list[float]:
    """Embed a single query string with `prefix_query` (no heading path —
    there isn't one). Used by `stw find` for the vector leg."""
    cfg = ws.config["embed"]
    cmd = cfg.get("cmd") or []
    if not cmd:
        raise NoEmbedder()
    prefix_query = cfg.get("prefix_query", "")
    dim = int(cfg.get("dim", 384))
    try:
        vectors = _run_sidecar(cmd, [("q", f"{prefix_query}{text}")])
    except RuntimeError as e:
        raise EmbedFailed(f"sidecar failed embedding the query: {e}", 0) from None
    vec = vectors["q"]
    if len(vec) != dim:
        raise DimMismatch(dim, len(vec))
    return vec
