"""stw rm PATH — remove a file, refusing if anything still links to it.

Snapshots the content to history first (so `stw restore` can bring it back)
then removes it from disk and the index. Refuses when backlinks exist unless
--force, naming the sources (plan.md §3) - a plain `rm` gives no such warning.
"""

from __future__ import annotations

import argparse

from .. import db, history, out
from ..errors import Backlinks, NotFound
from ..index import backlinks as backlinks_of
from ..index import remove


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path")
    parser.add_argument("--force", action="store_true", help="remove even if other files link to it")


def run(ws, conn, args) -> int:
    rel = ws.rel(args.path)
    abspath = ws.abs(rel)
    if not abspath.exists():
        raise NotFound(rel)

    srcs = sorted({r["src_path"] for r in backlinks_of(conn, rel)})
    if srcs and not args.force:
        raise Backlinks(rel, srcs)

    size = abspath.stat().st_size
    with db.tx(conn):
        history.snapshot(ws, conn, rel, "rm")
        remove(conn, rel)
        abspath.unlink()

    human = f"removed {rel} · {out.fmt_size(size)}"
    if srcs:
        human += f" · {len(srcs)} backlink(s) left dangling (--force)"
    out.emit(human, {"path": rel, "size": size, "backlinks": srcs, "forced": bool(srcs)})
    return 0
