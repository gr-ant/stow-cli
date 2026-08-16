"""Outbound links from a file (plan.md §9)."""

from __future__ import annotations

import argparse

from .. import out
from ..index import stale_check


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path")


def run(ws, conn, args) -> int:
    rel = ws.rel(args.path)
    if stale_check(ws, conn, rel):
        out.warn("W_STALE", f"{rel} changed on disk since it was indexed", "Run `stw sync`.")

    rows = list(
        conn.execute(
            "SELECT src_line, raw, target_path, target_anchor, resolved "
            "FROM links WHERE src_path = ? ORDER BY src_line",
            (rel,),
        )
    )

    lines = []
    data = []
    for r in rows:
        target = r["target_path"] if r["resolved"] else "UNRESOLVED"
        anchor = f"#{r['target_anchor']}" if r["target_anchor"] else ""
        lines.append(f"{rel}:{r['src_line']}  {r['raw']}  -> {target}{anchor}")
        data.append(
            {
                "line": r["src_line"],
                "raw": r["raw"],
                "target_path": r["target_path"],
                "target_anchor": r["target_anchor"],
                "resolved": bool(r["resolved"]),
            }
        )

    human = "\n".join(lines) if lines else f"{rel}: no outbound links"
    out.emit(human, {"path": rel, "links": data, "count": len(data)})
    return 0
