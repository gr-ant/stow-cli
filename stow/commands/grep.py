"""Regex/literal search across included files on disk (plan.md §8).

Reads straight from disk, not the index — always fresh, no stale_check needed.
Output is context the agent pays for, so total lines are capped and the cap is
disclosed in a footer rather than silently truncating.
"""

from __future__ import annotations

import argparse
import re

from .. import out
from ..errors import Usage

CAP = 200


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("pattern")
    parser.add_argument("--under", default=None, help="restrict to a subtree")
    parser.add_argument("-C", "--context", type=int, default=0, dest="context")
    parser.add_argument("-l", "--files-with-matches", action="store_true", dest="list_only")
    parser.add_argument("-i", "--ignore-case", action="store_true", dest="ignore_case")
    parser.add_argument("--fixed", action="store_true", help="literal match, not regex")


def run(ws, conn, args) -> int:
    pattern = re.escape(args.pattern) if args.fixed else args.pattern
    try:
        rx = re.compile(pattern, re.IGNORECASE if args.ignore_case else 0)
    except re.error as e:
        raise Usage(f"invalid pattern: {e}", "Use --fixed for a literal match.") from None

    paths = ws.walk()
    if args.under:
        u = args.under.rstrip("/")
        paths = [p for p in paths if p == u or p.startswith(u + "/")]

    files_matched: list[str] = []
    hit_rows: list[dict] = []

    for rel in paths:
        p = ws.abs(rel)
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.split("\n")
        hit_idx = [i for i, ln in enumerate(lines) if rx.search(ln)]
        if not hit_idx:
            continue
        files_matched.append(rel)
        if args.list_only:
            continue
        shown = set()
        for i in hit_idx:
            lo, hi = max(0, i - args.context), min(len(lines) - 1, i + args.context)
            for j in range(lo, hi + 1):
                shown.add(j)
        hitset = set(hit_idx)
        for j in sorted(shown):
            hit_rows.append(
                {"path": rel, "line": j + 1, "text": lines[j], "match": j in hitset}
            )

    if args.list_only:
        truncated = len(files_matched) > CAP
        shown_files = files_matched[:CAP]
        lines_out = list(shown_files)
        data = {"pattern": args.pattern, "files": shown_files, "truncated": truncated}
    else:
        truncated = len(hit_rows) > CAP
        shown_rows = hit_rows[:CAP]
        lines_out = [
            f"{r['path']}:{r['line']}: {r['text']}"
            if r["match"]
            else f"{r['path']}-{r['line']}- {r['text']}"
            for r in shown_rows
        ]
        data = {"pattern": args.pattern, "matches": shown_rows, "truncated": truncated}

    if truncated:
        lines_out.append(f"… output capped at {CAP} lines. Narrow with --under or -l.")
        data["cap"] = CAP

    human = "\n".join(lines_out) if lines_out else "(no matches)"
    out.emit(human, data)
    return 0
