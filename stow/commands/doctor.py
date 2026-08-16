"""Workspace health report (plan.md §9).

A report, not a failure: exits 1 when problems exist but never raises
StowError — `doctor` must always finish and print what it found.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .. import md, out

DEFAULT_N = 10


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--all", action="store_true", help="show every example, not just the first N")


def run(ws, conn, args) -> int:
    cap = None if args.all else DEFAULT_N
    cfg_dim = int(ws.config["embed"].get("dim", 384))

    broken, ambiguous = _link_problems(conn)
    orphans = [
        r["path"]
        for r in conn.execute(
            "SELECT path FROM files WHERE kind = 'md' AND path NOT IN "
            "(SELECT target_path FROM links WHERE target_path IS NOT NULL) ORDER BY path"
        )
    ]
    dups = [
        f"{r['path']}#{r['heading_path']}"
        for r in conn.execute(
            "SELECT path, heading_path, COUNT(*) c FROM headings "
            "GROUP BY path, heading_path HAVING c > 1 ORDER BY path"
        )
    ]
    dirty = [
        r["chunk_id"]
        for r in conn.execute("SELECT chunk_id FROM chunks WHERE dirty = 1 ORDER BY chunk_id")
    ]
    orphan_emb = [
        r["embed_sha"]
        for r in conn.execute(
            "SELECT embed_sha FROM embeddings WHERE embed_sha NOT IN (SELECT embed_sha FROM chunks)"
        )
    ]
    dim_mismatch = [
        {"model": r["model"], "dim": r["dim"]}
        for r in conn.execute("SELECT DISTINCT model, dim FROM embeddings WHERE dim != ?", (cfg_dim,))
    ]

    categories: list[tuple[str, str, list, callable]] = [
        ("broken_links", "broken links", broken, lambda x: f"{x['src_path']}:{x['src_line']}  {x['raw']}"),
        (
            "ambiguous_links",
            "ambiguous wiki targets",
            ambiguous,
            lambda x: f"{x['src_path']}:{x['src_line']}  {x['raw']} -> {', '.join(x['candidates'])}",
        ),
        ("orphans", "orphans (zero backlinks)", orphans, lambda x: x),
        ("duplicate_headings", "duplicate headings", dups, lambda x: x),
        ("dirty_chunks", "dirty chunks", dirty, lambda x: x),
        ("orphaned_embeddings", "orphaned embeddings", orphan_emb, lambda x: x),
        (
            "dim_mismatches",
            "dim mismatches",
            dim_mismatch,
            lambda x: f"model={x['model']} dim={x['dim']} (config={cfg_dim})",
        ),
    ]

    total = sum(len(rows) for _, _, rows, _ in categories)
    lines = ["doctor: clean" if total == 0 else f"doctor: {out.fmt_count(total, 'problem')}"]
    data: dict = {"total": total, "categories": {}}
    for key, title, rows, fmt in categories:
        lines.append(f"{title} ({len(rows)})")
        shown = rows if cap is None else rows[:cap]
        for x in shown:
            lines.append(f"  {fmt(x)}")
        if cap is not None and len(rows) > cap:
            lines.append(f"  … {len(rows) - cap} more, run with --all")
        data["categories"][key] = rows

    out.emit("\n".join(lines), data)
    return 1 if total else 0


def _link_problems(conn) -> tuple[list[dict], list[dict]]:
    """Split unresolved links into truly broken vs. ambiguous-by-basename."""
    names: dict[str, list[str]] = {}
    for r in conn.execute("SELECT path FROM files"):
        names.setdefault(Path(r["path"]).name.lower(), []).append(r["path"])
        names.setdefault(Path(r["path"]).stem.lower(), []).append(r["path"])

    broken, ambiguous = [], []
    rows = conn.execute(
        "SELECT src_path, src_line, raw FROM links WHERE resolved = 0 ORDER BY src_path, src_line"
    )
    for r in rows:
        parsed = md.parse_links(r["raw"])
        target = parsed[0].target if parsed else r["raw"]
        cands = sorted(
            set(names.get(Path(target).name.lower(), []) + names.get(Path(target).stem.lower(), []))
        )
        entry = {"src_path": r["src_path"], "src_line": r["src_line"], "raw": r["raw"]}
        if len(cands) > 1:
            ambiguous.append({**entry, "candidates": cands})
        else:
            broken.append(entry)
    return broken, ambiguous
