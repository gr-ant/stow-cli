"""stw gc — prune history versions past history.keep, drop orphaned embeddings."""

from __future__ import annotations

import argparse

from .. import db, history, out


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--keep", type=int, default=None, help="override config history.keep")


def run(ws, conn, args) -> int:
    keep = args.keep if args.keep is not None else int(ws.config.get("history", {}).get("keep", 50))

    with db.tx(conn):
        hist_stats = history.prune(ws, conn, keep)
        orphans = [
            r["embed_sha"]
            for r in conn.execute(
                "SELECT embed_sha FROM embeddings WHERE embed_sha NOT IN "
                "(SELECT DISTINCT embed_sha FROM chunks)"
            )
        ]
        if orphans:
            conn.executemany(
                "DELETE FROM embeddings WHERE embed_sha = ?", [(s,) for s in orphans]
            )

    human = (
        f"gc: pruned {hist_stats['versions_pruned']} version(s), "
        f"reclaimed {hist_stats['objects_deleted']} object(s), "
        f"dropped {len(orphans)} orphaned embedding(s)"
    )
    out.emit(human, {
        "versions_pruned": hist_stats["versions_pruned"],
        "objects_deleted": hist_stats["objects_deleted"],
        "embeddings_dropped": len(orphans),
    })
    return 0
