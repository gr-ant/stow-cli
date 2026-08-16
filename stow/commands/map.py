"""Render the workspace orientation file (plan.md §8).

Without --write, prints the (possibly filtered) map to stdout for a quick
look. --write renders the default view and persists it to map.md — the file
other write commands refresh after a mutation.
"""

from __future__ import annotations

import argparse

from .. import out
from ..render import render_map, write_map


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--depth", type=int, default=None)
    parser.add_argument("--under", default=None, help="restrict to a subtree")
    parser.add_argument("--write", action="store_true", help="write map.md instead of printing")


def run(ws, conn, args) -> int:
    if args.write:
        path = write_map(ws, conn)
        rel = path.relative_to(ws.root).as_posix()
        out.emit(f"wrote {rel}", {"path": rel})
        return 0

    content = render_map(ws, conn, depth=args.depth, under=args.under)
    if out.is_json():
        out.emit(data={"content": content})
    else:
        out.raw(content)
    return 0
