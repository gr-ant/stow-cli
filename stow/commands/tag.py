"""stw tag PATH +stale -wip — edit a file's frontmatter tags in place.

Bare words are treated as additions: `stw tag PATH stale` == `stw tag PATH
+stale`. Snapshots first, then rewrites frontmatter and reindexes.
"""

from __future__ import annotations

import argparse

from .. import db, history, md, out
from ..errors import NotFound, Usage
from ..index import reindex
from ..workspace import kind_of


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path")
    # REMAINDER, not "+": "-wip" looks like an option to argparse otherwise and
    # gets rejected as unrecognized rather than reaching us as a tag to remove.
    parser.add_argument("changes", nargs=argparse.REMAINDER,
                         help="+tag to add, -tag to remove, bare word == +tag")


def run(ws, conn, args) -> int:
    rel = ws.rel(args.path)
    abspath = ws.abs(rel)
    if not abspath.exists():
        raise NotFound(rel)
    if kind_of(rel) != "md":
        raise Usage(f"{rel} is not markdown", "`stw tag` edits frontmatter, which only markdown files have.")
    if not args.changes:
        raise Usage("no tag changes given", "Use `stw tag PATH +stale -wip`.")

    additions: list[str] = []
    removals: list[str] = []
    for tok in args.changes:
        if tok.startswith("-") and len(tok) > 1:
            removals.append(tok[1:])
        elif tok.startswith("+") and len(tok) > 1:
            additions.append(tok[1:])
        else:
            additions.append(tok)

    text = abspath.read_text(encoding="utf-8", errors="replace")
    fm, body, _ = md.split_frontmatter(text)
    tags = fm.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    tags = list(tags)
    for t in additions:
        if t and t not in tags:
            tags.append(t)
    for t in removals:
        if t in tags:
            tags.remove(t)
    fm["tags"] = tags
    new_text = md.render_frontmatter(fm) + body.lstrip("\n")

    with db.tx(conn):
        history.snapshot(ws, conn, rel, "tag")
        abspath.write_text(new_text, encoding="utf-8")
        stats = reindex(ws, conn, rel)

    human = f"tagged {rel} · [{', '.join(tags)}]"
    out.emit(human, {"path": rel, "tags": tags, **stats})
    if stats["dup_headings"]:
        out.warn(
            "W_DUP_HEADING",
            f"{rel} has duplicate heading path(s): {', '.join(stats['dup_headings'])}.",
            "Use a fuller heading path in `stw set`/`stw read --section` to disambiguate.",
        )
    return 0
