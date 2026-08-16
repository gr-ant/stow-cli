"""stw sql PATH QUERY — run SQL against a DuckDB artifact (plan.md §4, §10).

`import duckdb` is lazy, same reasoning as stow/commands/db.py. Bare SELECTs
get a default LIMIT 100 so an agent can never accidentally dump 40k rows;
the footer says so and names how many rows actually exist.
"""

from __future__ import annotations

import argparse
import json as jsonlib
import re

from .. import out
from ..errors import StowError
from .db import import_duckdb, guard_not_index, refresh_tables

DEFAULT_LIMIT = 100
_SELECT_RE = re.compile(r"^\s*(select|with)\b", re.I)
_HAS_LIMIT_RE = re.compile(r"\blimit\s+\d+\s*;?\s*$", re.I)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path")
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=None, help=f"default {DEFAULT_LIMIT}")


def run(ws, conn, args) -> int:
    duckdb = import_duckdb()

    rel = ws.rel(args.path)
    guard_not_index(ws, rel)
    abspath = ws.abs(rel)
    if not abspath.exists():
        raise StowError("E_NOT_FOUND", f"{rel} is not a DuckDB file", "Run `stw db new` first.")

    query = args.query
    is_select = bool(_SELECT_RE.match(query))
    limit = args.limit if args.limit is not None else DEFAULT_LIMIT

    dconn = duckdb.connect(str(abspath))
    try:
        applied_limit = False
        total = None
        run_query = query
        if is_select and not _HAS_LIMIT_RE.search(query):
            stripped = query.strip().rstrip(";")
            try:
                total = dconn.execute(f"SELECT COUNT(*) FROM ({stripped}) AS _stw_count").fetchone()[0]
            except Exception:
                total = None  # queries DuckDB can't wrap in a count subquery just run unlimited
            if total is not None and total > limit:
                run_query = f"{stripped} LIMIT {limit}"
                applied_limit = True

        result = dconn.execute(run_query)
        if is_select:
            cols = [d[0] for d in result.description] if result.description else []
            rows = result.fetchall()
        else:
            cols, rows = [], []
    finally:
        dconn.close()

    if not is_select:
        refresh_tables(ws, conn, rel, abspath)

    data: dict = {"path": rel, "columns": cols, "rows": [list(r) for r in rows]}

    if is_select:
        lines = []
        if cols:
            lines.append(" | ".join(cols))
        for r in rows:
            lines.append(" | ".join("" if v is None else str(v) for v in r))
        if applied_limit:
            footer = f"… LIMIT {limit} applied · {total} rows total. Use --limit to see more."
            lines.append(footer)
            data["truncated"] = True
            data["total_rows"] = total
        out.emit("\n".join(lines) if lines else "0 rows", data)
    else:
        out.emit(f"ok · {rel}", data)
    return 0
