"""What links to a file — the highest-value read command in the tool (plan.md §9).

"What else references this" is the question an agent most needs answered and
can least afford to answer by grepping the tree.
"""

from __future__ import annotations

import argparse

from .. import out
from ..index import backlinks as backlinks_of
from ..index import stale_check


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path")


def run(ws, conn, args) -> int:
    rel = ws.rel(args.path)
    if stale_check(ws, conn, rel):
        out.warn("W_STALE", f"{rel} changed on disk since it was indexed", "Run `stw sync`.")

    rows = backlinks_of(conn, rel)
    lines = [f"{r['src_path']}:{r['src_line']}  {r['raw']}" for r in rows]
    data = [{"src_path": r["src_path"], "src_line": r["src_line"], "raw": r["raw"]} for r in rows]

    human = "\n".join(lines) if lines else f"{rel}: no backlinks"
    out.emit(human, {"path": rel, "backlinks": data, "count": len(data)})
    return 0
