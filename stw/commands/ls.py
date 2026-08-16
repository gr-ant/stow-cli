"""List files by glob, tag, or kind (plan.md §8). Terse: one line per file."""

from __future__ import annotations

import argparse
import fnmatch
import json

from .. import out


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("glob", nargs="?", default=None, help="e.g. 'research/**' or '*.md'")
    parser.add_argument("--tag", action="append", default=[], help="repeatable; AND semantics")
    parser.add_argument("--kind", choices=["md", "db"])
    parser.add_argument("--sort", choices=["path", "mtime", "size"], default="path")


def run(ws, conn, args) -> int:
    rows = list(conn.execute("SELECT path, kind, size, mtime_ns, about, tags FROM files"))

    if args.glob:
        rows = [r for r in rows if fnmatch.fnmatch(r["path"], args.glob)]
    if args.kind:
        rows = [r for r in rows if r["kind"] == args.kind]
    if args.tag:
        want = set(args.tag)
        rows = [r for r in rows if want.issubset(set(json.loads(r["tags"] or "[]")))]

    sort_key = {
        "path": lambda r: r["path"],
        "mtime": lambda r: -r["mtime_ns"],
        "size": lambda r: -r["size"],
    }[args.sort]
    rows.sort(key=sort_key)

    lines = []
    data = []
    for r in rows:
        tags = json.loads(r["tags"] or "[]")
        tag_str = " ".join(f"#{t}" for t in tags)
        about = r["about"] or ""
        line = f"{r['path']:<40} {out.fmt_size(r['size']):>7}  {tag_str:<20}  {about}".rstrip()
        lines.append(line)
        data.append(
            {"path": r["path"], "kind": r["kind"], "size": r["size"], "tags": tags, "about": about}
        )

    human = "\n".join(lines) if lines else "(no files)"
    out.emit(human, {"files": data, "count": len(data)})
    return 0
