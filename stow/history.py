"""Content-addressed history store (plan.md §3 'History').

Every destructive write snapshots the CURRENT on-disk bytes of a path -
before the change lands - into `.stow/objects/ab/cdef...`, zlib-compressed
and keyed by sha256. Stow already hashes every file it touches, so this
costs one zlib write and an index row; without it `stw write` would be an
unrecoverable clobber on a workspace whose whole premise is constant editing.
"""

from __future__ import annotations

import sqlite3
import zlib
from pathlib import Path

from .errors import StowError
from .hashing import sha256_bytes
from .index import now_iso
from .workspace import Workspace


def _object_path(ws: Workspace, sha: str) -> Path:
    return ws.objects_dir / sha[:2] / sha[2:]


def snapshot(ws: Workspace, conn: sqlite3.Connection, rel: str, command: str) -> str | None:
    """Snapshot the CURRENT bytes of `rel` before a destructive change touches it.

    No-op, returning None, when history is disabled, the path has no bytes on
    disk yet (nothing to save), or the current sha matches the most recent
    snapshot for this path - we never record the same content twice in a row.
    """
    if not ws.config.get("history", {}).get("enabled", True):
        return None
    p = ws.abs(rel)
    if not p.exists():
        return None
    data = p.read_bytes()
    sha = sha256_bytes(data)

    last = conn.execute(
        "SELECT sha FROM versions WHERE path = ? ORDER BY seq DESC LIMIT 1", (rel,)
    ).fetchone()
    if last is not None and last["sha"] == sha:
        return sha

    obj = _object_path(ws, sha)
    if not obj.exists():
        obj.parent.mkdir(parents=True, exist_ok=True)
        obj.write_bytes(zlib.compress(data))

    conn.execute(
        "INSERT INTO versions(sha, path, size, command, created_at) VALUES(?,?,?,?,?)",
        (sha, rel, len(data), command, now_iso()),
    )
    return sha


def versions(conn: sqlite3.Connection, rel: str) -> list[sqlite3.Row]:
    """A path's snapshots, newest first."""
    return list(
        conn.execute(
            "SELECT sha, path, size, command, created_at, seq FROM versions "
            "WHERE path = ? ORDER BY seq DESC",
            (rel,),
        )
    )


def rekey(conn: sqlite3.Connection, old_rel: str, new_rel: str) -> int:
    """Carry a path's history across a `mv`.

    Versions are keyed by path, so without this a moved file looks like it has
    no history at all -- `stw log`/`stw undo` would report nothing seconds after
    the agent wrote it.
    """
    cur = conn.execute("UPDATE versions SET path = ? WHERE path = ?", (new_rel, old_rel))
    return cur.rowcount or 0


def read_version(ws: Workspace, sha: str) -> bytes:
    obj = _object_path(ws, sha)
    if not obj.exists():
        raise StowError(
            "E_NOT_FOUND", f"no history object {sha[:8]}…",
            "It may have been pruned by `stw gc`.",
        )
    return zlib.decompress(obj.read_bytes())


def prune(ws: Workspace, conn: sqlite3.Connection, keep: int) -> dict:
    """Drop versions beyond `keep` per path; delete objects nothing references anymore.

    Returns {"versions_pruned": int, "objects_deleted": int}.
    """
    paths = [r["path"] for r in conn.execute("SELECT DISTINCT path FROM versions")]
    pruned = 0
    for rel in paths:
        rows = conn.execute(
            "SELECT seq FROM versions WHERE path = ? ORDER BY seq DESC", (rel,)
        ).fetchall()
        for r in rows[keep:]:
            conn.execute("DELETE FROM versions WHERE seq = ?", (r["seq"],))
            pruned += 1

    referenced = {r["sha"] for r in conn.execute("SELECT DISTINCT sha FROM versions")}
    deleted = 0
    objects_dir = ws.objects_dir
    if objects_dir.exists():
        for shard in objects_dir.iterdir():
            if not shard.is_dir():
                continue
            for obj in shard.iterdir():
                if f"{shard.name}{obj.name}" not in referenced:
                    obj.unlink()
                    deleted += 1
            try:
                shard.rmdir()
            except OSError:
                pass
    return {"versions_pruned": pruned, "objects_deleted": deleted}
