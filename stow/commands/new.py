"""stw new PATH --about TEXT --tags a,b [--title T] — create a file with frontmatter.

Refuses if the path already exists (that's what `stw write` is for). Handles
the frontmatter schema so the agent never has to remember it (plan.md §3).
Body is optional and comes from stdin if any is piped in.
"""

from __future__ import annotations

import argparse
import sys

from .. import db, md, out
from ..errors import Exists
from ..index import now_iso, reindex, reresolve_incoming


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path")
    parser.add_argument("--about", default="", help="one-line purpose, shown in the map")
    parser.add_argument("--tags", default="", help="comma-separated tags")
    parser.add_argument("--title", default=None)


def run(ws, conn, args) -> int:
    rel = ws.rel(args.path)
    abspath = ws.abs(rel)
    if abspath.exists():
        raise Exists(rel)

    body = _read_stdin()
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    fm = {
        "title": args.title or _default_title(rel),
        "about": args.about,
        "tags": tags,
        "created": now_iso(),
    }
    text = md.render_frontmatter(fm) + "\n" + body.lstrip("\n")

    abspath.parent.mkdir(parents=True, exist_ok=True)

    with db.tx(conn):
        abspath.write_text(text, encoding="utf-8")
        stats = reindex(ws, conn, rel)
        reresolve_incoming(ws, conn)

    _maybe_regen_map(ws, conn)
    _confirm("stowed", rel, stats)
    return 0


def _default_title(rel: str) -> str:
    return rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]


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
