"""stw mv OLD NEW — move a file and rewrite every inbound link to it.

Plain `mv` silently breaks every `[[wiki link]]` and `[text](path.md)` that
pointed at the old path. This moves the file, rewrites the raw link text in
every *other* file that referenced it (found via the already-resolved
`links` table, so aliasing and basename matches are covered too), and
reindexes both the moved file and everything that was rewritten.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from .. import db, history, md, out
from ..errors import Exists, NotFound, stwError
from ..index import backlinks as backlinks_of
from ..index import reindex, remove, reresolve_incoming


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("old")
    parser.add_argument("new")
    parser.add_argument("--force", action="store_true", help="overwrite NEW if it already exists")


def run(ws, conn, args) -> int:
    old_rel = ws.rel(args.old)
    old_abs = ws.abs(old_rel)
    if not old_abs.exists():
        raise NotFound(old_rel)

    new_rel = ws.rel(args.new)
    new_abs = ws.abs(new_rel)
    if new_abs.is_dir():
        new_rel = ws.rel(str(Path(args.new) / Path(old_rel).name))
        new_abs = ws.abs(new_rel)
    if new_rel == old_rel:
        raise stwError("E_USAGE", "source and destination are the same path")
    if new_abs.exists() and not args.force:
        raise Exists(new_rel)

    rows = backlinks_of(conn, old_rel)   # [{src_path, src_line, raw}], excludes old_rel itself
    by_file: dict[str, list] = {}
    for r in rows:
        by_file.setdefault(r["src_path"], []).append(r)

    with db.tx(conn):
        history.snapshot(ws, conn, old_rel, "mv")
        new_abs.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old_abs), str(new_abs))
        # Drop the old registry row, or the file shows up twice in `map`/`ls`,
        # and carry its history across so `log`/`undo` still work after a move.
        remove(conn, old_rel)
        history.rekey(conn, old_rel, new_rel)

        rewritten = 0
        files_touched: list[str] = []
        for src, links_here in by_file.items():
            src_abs = ws.abs(src)
            if not src_abs.exists():
                continue
            text = src_abs.read_text(encoding="utf-8", errors="replace")
            lines = text.split("\n")
            changed = False
            for r in links_here:
                parsed = md.parse_links(r["raw"])
                if not parsed:
                    continue
                lk = parsed[0]
                new_target = _rewritten_target(new_rel, lk.target)
                new_raw = _rebuild_link(lk, new_target)
                idx = r["src_line"] - 1
                if 0 <= idx < len(lines) and r["raw"] in lines[idx]:
                    lines[idx] = lines[idx].replace(r["raw"], new_raw, 1)
                    changed = True
                    rewritten += 1
            if changed:
                history.snapshot(ws, conn, src, "mv")
                src_abs.write_text("\n".join(lines), encoding="utf-8")
                files_touched.append(src)

        stats = reindex(ws, conn, new_rel)
        for src in files_touched:
            reindex(ws, conn, src)
        reresolve_incoming(ws, conn)

    _maybe_regen_map(ws, conn)

    human = f"moved {old_rel} -> {new_rel} · {rewritten} link(s) rewritten in {len(files_touched)} file(s)"
    if stats["dup_headings"]:
        out.warn(
            "W_DUP_HEADING",
            f"{new_rel} has duplicate heading path(s): {', '.join(stats['dup_headings'])}.",
            "Use a fuller heading path in `stw set`/`stw read --section` to disambiguate.",
        )
    out.emit(human, {
        "old": old_rel, "new": new_rel, "rewritten": rewritten,
        "files_touched": sorted(files_touched), **stats,
    })
    return 0


def _rewritten_target(new_rel: str, orig_target: str) -> str:
    """Preserve whether the original link spelled out an extension."""
    had_ext = orig_target.strip().lower().endswith((".md", ".markdown", ".db"))
    if had_ext or not new_rel.lower().endswith(".md"):
        return new_rel
    return new_rel[: -len(".md")]


def _rebuild_link(lk: md.Link, new_target: str) -> str:
    if lk.kind == "wiki":
        inner = new_target
        if lk.anchor:
            inner += f"#{lk.anchor}"
        if lk.alias:
            inner += f"|{lk.alias}"
        return f"[[{inner}]]"
    href = new_target + (f"#{lk.anchor}" if lk.anchor else "")
    alias = lk.alias if lk.alias is not None else ""
    return f"[{alias}]({href})"


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
