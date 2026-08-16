"""stw undo PATH — restore the previous version.

The current file is snapshotted first, so undo is itself undoable: running
`stw undo` twice is a no-op on the file's content (plan.md §3).
"""

from __future__ import annotations

import argparse

from .. import db, history, out
from ..errors import stwError
from ..hashing import short
from ..index import reindex, reresolve_incoming


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path")


def run(ws, conn, args) -> int:
    rel = ws.rel(args.path)
    rows = history.versions(conn, rel)
    if not rows:
        raise stwError("E_NOT_FOUND", f"no history for {rel}", "Nothing to undo.")

    target = rows[0]
    data = history.read_version(ws, target["sha"])
    abspath = ws.abs(rel)
    is_new = not abspath.exists()

    with db.tx(conn):
        history.snapshot(ws, conn, rel, "undo")   # snapshot current HEAD before it's clobbered
        abspath.parent.mkdir(parents=True, exist_ok=True)
        abspath.write_bytes(data)
        stats = reindex(ws, conn, rel)
        if is_new:
            reresolve_incoming(ws, conn)

    human = (
        f"undid {rel} · restored {short(target['sha'])} "
        f"(from {target['command']} @ {target['created_at']}) · {out.fmt_size(stats['size'])}"
    )
    out.emit(human, {"path": rel, "restored_sha": target["sha"], **stats})
    return 0
