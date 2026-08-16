"""Repair the index from files on disk (plan.md §12).

The repair tool for the times a file gets edited behind stw's back — sed, a
human in the folder, whatever. On an untouched workspace this must read no
file contents: stat every included path, compare (size, mtime_ns) against the
registry, and only hash on a mismatch (or under --force).
"""

from __future__ import annotations

import argparse

from .. import out
from ..db import tx
from ..hashing import sha256_bytes
from ..index import reindex, remove


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--force", action="store_true", help="rehash every file, not just mismatches")
    parser.add_argument(
        "--prune", action="store_true", default=True, help="remove registry rows for missing files (default on)"
    )
    parser.add_argument("--no-prune", dest="prune", action="store_false", help="keep rows for missing files")


def run(ws, conn, args) -> int:
    on_disk = set(ws.walk())
    registered = {
        r["path"]: r for r in conn.execute("SELECT path, size, mtime_ns, sha256 FROM files")
    }

    changed = new = removed = unchanged = 0

    with tx(conn):
        for rel in sorted(on_disk):
            row = registered.get(rel)
            if row is None:
                reindex(ws, conn, rel)
                new += 1
                continue

            st = ws.abs(rel).stat()
            stat_mismatch = st.st_size != row["size"] or st.st_mtime_ns != row["mtime_ns"]
            if not stat_mismatch and not args.force:
                unchanged += 1
                continue

            sha = sha256_bytes(ws.abs(rel).read_bytes())
            if sha == (row["sha256"] or ""):
                unchanged += 1
                if stat_mismatch:
                    # content is identical but stat moved (e.g. touch) — keep the
                    # registry's stat fresh without a full reparse.
                    conn.execute(
                        "UPDATE files SET size = ?, mtime_ns = ? WHERE path = ?",
                        (st.st_size, st.st_mtime_ns, rel),
                    )
                continue

            reindex(ws, conn, rel)
            changed += 1

        if args.prune:
            for rel in sorted(registered):
                if rel not in on_disk and not ws.abs(rel).exists():
                    remove(conn, rel)
                    removed += 1

    human = f"synced · {changed} changed · {new} new · {removed} removed · {unchanged} unchanged"
    out.emit(human, {"changed": changed, "new": new, "removed": removed, "unchanged": unchanged})
    return 0
