"""stw append PATH < content — append stdin to a file, keeping one blank line.

Creates the file (with frontmatter injected, for markdown) if it doesn't
exist yet - append is not required to fail just because nothing was there.
The prior bytes are snapshotted to history first (plan.md §3).
"""

from __future__ import annotations

import argparse
import sys

from .. import db, history, md, out
from ..index import reindex, reresolve_incoming
from ..workspace import kind_of


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path")


def run(ws, conn, args) -> int:
    rel = ws.rel(args.path)
    abspath = ws.abs(rel)
    is_new = not abspath.exists()
    addition = _read_stdin()

    if is_new:
        text = md.ensure_frontmatter(addition, {}) if kind_of(rel) == "md" else addition
    else:
        old = abspath.read_text(encoding="utf-8", errors="replace")
        text = old.rstrip("\n") + "\n\n" + addition.lstrip("\n")

    abspath.parent.mkdir(parents=True, exist_ok=True)

    with db.tx(conn):
        history.snapshot(ws, conn, rel, "append")
        abspath.write_text(text, encoding="utf-8")
        stats = reindex(ws, conn, rel)
        if is_new:
            reresolve_incoming(ws, conn)

    _maybe_regen_map(ws, conn)
    _confirm("appended", rel, stats)
    return 0


def _read_stdin() -> str:
    if sys.stdin.isatty():
        return ""
    try:
        return sys.stdin.read()
    except Exception:
        return ""


def _confirm(verb: str, label: str, stats: dict) -> None:
    link_bit = f"{stats['links']} links"
    if stats["unresolved"]:
        link_bit += f" ({stats['unresolved']} unresolved)"
    human = f"{verb} {label} · {out.fmt_size(stats['size'])} · {stats['headings']} headings · {link_bit}"
    out.emit(human, {"ok": True, **stats})
    if stats["dup_headings"]:
        out.warn(
            "W_DUP_HEADING",
            f"{label} has duplicate heading path(s): {', '.join(stats['dup_headings'])}.",
            "Use a fuller heading path in `stw set`/`stw read --section` to disambiguate.",
        )


def _maybe_regen_map(ws, conn) -> None:
    if ws.config.get("map", {}).get("regenerate") != "on-write":
        return
    try:
        from ..render import write_map   # owned by another agent, may not exist yet
    except ImportError:
        write_map = None
    if write_map is None:
        return
    try:
        write_map(ws, conn)
    except Exception:
        pass
