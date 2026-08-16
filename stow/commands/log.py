"""stw log PATH — version history for a path: short sha, size, command, when."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from .. import history, out
from ..hashing import short


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path")
    parser.add_argument("-n", "--limit", type=int, default=20, help="max versions to show")


def run(ws, conn, args) -> int:
    rel = ws.rel(args.path)
    rows = history.versions(conn, rel)[: args.limit]

    data = [
        {"sha": r["sha"], "size": r["size"], "command": r["command"], "created_at": r["created_at"]}
        for r in rows
    ]

    if not rows:
        out.emit(f"no history for {rel}", {"path": rel, "versions": data})
        return 0

    lines = [f"{rel} · {len(rows)} version(s)"]
    for r in rows:
        lines.append(
            f"  {short(r['sha'])}  {out.fmt_size(r['size']):>7}  {r['command']:<8}  {_relative(r['created_at'])}"
        )
    out.emit("\n".join(lines), {"path": rel, "versions": data})
    return 0


def _relative(iso_ts: str) -> str:
    try:
        then = datetime.strptime(iso_ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return iso_ts
    secs = (datetime.now(timezone.utc) - then).total_seconds()
    if secs < 60:
        return "just now"
    for unit, size in (("y", 31536000), ("mo", 2592000), ("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{int(secs // size)}{unit} ago"
    return "just now"
