"""Print file content — whole file, a section, or a line range (plan.md §8).

Content goes through out.raw so it is never JSON-wrapped in human mode: an
agent piping this to a file should get exactly the bytes, nothing decorated.
"""

from __future__ import annotations

import argparse

from .. import md, out
from ..errors import NotFound, Usage
from ..index import stale_check


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", help="PATH, or PATH#Heading/Sub")
    parser.add_argument("--section", help="heading address, alternative to PATH#...")
    parser.add_argument("--lines", help="A:B, 1-based inclusive")
    parser.add_argument("--head", type=int, help="print only the first N lines")


def run(ws, conn, args) -> int:
    bare_path, addr = md.split_address(args.path)
    rel = ws.rel(bare_path)
    section = args.section or addr

    if stale_check(ws, conn, rel):
        out.warn("W_STALE", f"{rel} changed on disk since it was indexed", "Run `stw sync`.")

    p = ws.abs(rel)
    if not p.exists():
        raise NotFound(rel)
    text = p.read_text(encoding="utf-8", errors="replace")

    lines_desc: str | None = None
    if section:
        headings = md.parse_headings(text)
        h = md.resolve_section(rel, headings, section)
        data = text.encode("utf-8")
        content = data[h.byte_start : h.byte_end].decode("utf-8", errors="replace")
        lines_desc = f"{h.line_start}:{h.line_end}"
    elif args.lines:
        content, lines_desc = _slice_lines(text, args.lines)
    else:
        content = text

    if args.head is not None:
        content = "\n".join(content.split("\n")[: args.head])
        if content and not content.endswith("\n"):
            content += "\n"

    if out.is_json():
        out.emit(data={"path": rel, "section": section, "lines": lines_desc, "content": content})
    else:
        out.raw(content)
    return 0


def _slice_lines(text: str, spec: str) -> tuple[str, str]:
    a_s, _, b_s = spec.partition(":")
    try:
        a = int(a_s)
        b = int(b_s) if b_s else a
    except ValueError:
        raise Usage(f"invalid --lines value: {spec!r}", "Use A:B, e.g. --lines 10:40.") from None
    if a < 1 or b < a:
        raise Usage(f"invalid --lines range: {spec!r}", "Use A:B with 1 <= A <= B.")
    all_lines = text.split("\n")
    picked = all_lines[a - 1 : b]
    content = "\n".join(picked)
    if picked:
        content += "\n"
    return content, spec
