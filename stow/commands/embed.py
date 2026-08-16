"""stw embed [--all] [--limit N] — embed dirty chunks via the sidecar.

Off the write path by design (plan.md §6): writes only flag chunks dirty,
this command is where the sidecar actually gets called. Each batch commits
independently, so an interrupted run leaves durable progress.
"""

from __future__ import annotations

import argparse

from .. import out
from ..db import tx
from ..embedder import embed_dirty


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--all", action="store_true", help="re-embed everything (e.g. after a model change)")
    parser.add_argument("--limit", type=int, default=None, help="embed at most N chunks")


def run(ws, conn, args) -> int:
    if args.all:
        with tx(conn):
            conn.execute("UPDATE chunks SET dirty = 1")

    stats = embed_dirty(ws, conn, limit=args.limit)
    out.emit(
        f"embedded {stats['embedded']} chunks · {stats['batches']} batches · "
        f"{stats['remaining']} remaining",
        stats,
    )
    return 0
