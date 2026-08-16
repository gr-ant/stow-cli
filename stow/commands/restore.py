"""stw restore PATH@SHA (or PATH --sha SHA) — restore a specific version.

Matches on short sha prefixes; an ambiguous prefix is an error rather than a
guess. The current file is snapshotted first, same as `undo`.
"""

from __future__ import annotations

import argparse

from .. import db, history, out
from ..errors import StowError, Usage
from ..hashing import short
from ..index import reindex, reresolve_incoming


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("target", help="PATH@sha, or PATH with --sha")
    parser.add_argument("--sha", default=None)


def run(ws, conn, args) -> int:
    path_part, sha_part = args.target, args.sha
    if not sha_part and "@" in args.target:
        path_part, _, sha_part = args.target.partition("@")
    if not sha_part:
        raise Usage("no version given", "Use `stw restore PATH@sha` or `stw restore PATH --sha SHA`.")

    rel = ws.rel(path_part)
    rows = history.versions(conn, rel)
    matches = [r for r in rows if r["sha"].startswith(sha_part)]
    distinct = {m["sha"] for m in matches}
    if not matches:
        raise StowError(
            "E_NOT_FOUND", f"no version '{sha_part}' for {rel}",
            f"Run `stw log {rel}` to list them.",
        )
    if len(distinct) > 1:
        raise StowError(
            "E_AMBIGUOUS_SHA", f"'{sha_part}' matches {len(distinct)} versions of {rel}",
            "Use a longer prefix.",
        )

    target = matches[0]
    data = history.read_version(ws, target["sha"])
    abspath = ws.abs(rel)
    is_new = not abspath.exists()

    with db.tx(conn):
        history.snapshot(ws, conn, rel, "restore")
        abspath.parent.mkdir(parents=True, exist_ok=True)
        abspath.write_bytes(data)
        stats = reindex(ws, conn, rel)
        if is_new:
            reresolve_incoming(ws, conn)

    human = (
        f"restored {rel} @ {short(target['sha'])} "
        f"(from {target['command']} @ {target['created_at']}) · {out.fmt_size(stats['size'])}"
    )
    out.emit(human, {"path": rel, "restored_sha": target["sha"], **stats})
    return 0
