"""stw set PATH#Heading/Sub < content [--expect-sha SHA] — replace one section.

The largest token saving in the tool (plan.md §3): edit one section without
reading or rewriting the whole file. `--expect-sha` guards against clobbering
a section that changed since it was last read - it accepts either the full
content_sha or a short prefix of it (>= 8 chars is the documented case).
"""

from __future__ import annotations

import argparse
import sys

from .. import db, history, md, out
from ..errors import NotFound, StaleSection, Usage
from ..index import reindex


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("address", help="PATH#Heading/Sub")
    parser.add_argument("--expect-sha", default=None, help="content_sha (or a prefix) the section must currently have")


def run(ws, conn, args) -> int:
    path, addr = md.split_address(args.address)
    if not addr:
        raise Usage(f"'{args.address}' has no #heading address", "Use `stw set PATH#Heading/Sub`.")

    rel = ws.rel(path)
    abspath = ws.abs(rel)
    if not abspath.exists():
        raise NotFound(rel)

    text = abspath.read_text(encoding="utf-8", errors="replace")
    headings = md.parse_headings(text)
    h = md.resolve_section(rel, headings, addr)   # raises AmbiguousHeading / NoSuchHeading

    if args.expect_sha:
        want, got = args.expect_sha, h.content_sha
        if not got.startswith(want):
            raise StaleSection(rel, h.heading_path, want, got, f"L{h.line_start}-{h.line_end}")

    new_body = _read_stdin()
    new_text = md.replace_section(text, h, new_body)

    with db.tx(conn):
        history.snapshot(ws, conn, rel, "set")
        abspath.write_text(new_text, encoding="utf-8")
        stats = reindex(ws, conn, rel)

    _maybe_regen_map(ws, conn)
    label = f"{rel}#{h.heading_path}"
    _confirm("set", label, stats)
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
    out.emit(human, {"ok": True, "section": label, **stats})
    if stats["dup_headings"]:
        out.warn(
            "W_DUP_HEADING",
            f"{stats['path']} has duplicate heading path(s): {', '.join(stats['dup_headings'])}.",
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
