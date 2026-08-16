"""Seed a workspace through the library, so tests don't depend on other agents'
commands being finished yet."""

from __future__ import annotations

from pathlib import Path

from stw.db import connect
from stw.index import reindex, reresolve_incoming
from stw.workspace import Workspace


def seed(root: Path, files: dict[str, str]) -> Workspace:
    """Write files and index them. Returns the Workspace."""
    ws = Workspace.at(root)
    conn = connect(ws.index_path, create=True)
    try:
        for rel, content in files.items():
            p = ws.abs(rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        for rel in files:
            reindex(ws, conn, rel)
        # Links written before their target was seeded resolve on the second pass.
        reresolve_incoming(ws, conn)
        conn.commit()
    finally:
        conn.close()
    return ws
