"""Heading tree of a file (plan.md §10).

Exists so whole-file reads are rarely necessary: an agent can see the shape of
a document, and with --sha the exact content_sha it needs to pass to
`stw set --expect-sha`, without spending tokens on the body.
"""

from __future__ import annotations

import argparse

from .. import md, out
from ..errors import NotFound
from ..hashing import short
from ..index import stale_check


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path")
    parser.add_argument("--sha", action="store_true", help="show each section's content_sha")


def run(ws, conn, args) -> int:
    rel = ws.rel(args.path)
    if stale_check(ws, conn, rel):
        out.warn("W_STALE", f"{rel} changed on disk since it was indexed", "Run `stw sync`.")

    p = ws.abs(rel)
    if not p.exists():
        raise NotFound(rel)
    text = p.read_text(encoding="utf-8", errors="replace")
    headings = md.parse_headings(text)

    lines = [f"{rel} · {out.fmt_count(len(headings), 'heading')}"]
    rows = []
    for h in headings:
        indent = "  " * (h.level - 1)
        seg = f"{indent}{'#' * h.level} {h.text}  L{h.line_start}-{h.line_end}"
        if args.sha:
            seg += f"  {short(h.content_sha)}"
        lines.append(seg)
        rows.append(
            {
                "heading_path": h.heading_path,
                "level": h.level,
                "line_start": h.line_start,
                "line_end": h.line_end,
                "content_sha": h.content_sha if args.sha else None,
            }
        )

    out.emit("\n".join(lines), {"path": rel, "headings": rows})
    return 0
