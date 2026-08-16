"""stw db new/import/export/tables — DuckDB artifacts (plan.md §4).

`import duckdb` is lazy — it happens inside these function bodies only, so
`stw read` and friends never pay for it. stw's own index is SQLite and is
never addressable here: any path resolving to .stw/stw.db is rejected.
"""

from __future__ import annotations

import argparse
import json as jsonlib
from pathlib import Path

from .. import out
from ..db import tx
from ..errors import stwError
from ..index import reindex


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="subcmd", required=True)

    p_new = sub.add_parser("new", help="create an empty DuckDB file")
    p_new.add_argument("path")

    p_imp = sub.add_parser("import", help="import a CSV into a table")
    p_imp.add_argument("path")
    p_imp.add_argument("--csv", required=True)
    p_imp.add_argument("--as", dest="table", required=True)

    p_exp = sub.add_parser("export", help="export a table to CSV")
    p_exp.add_argument("path")
    p_exp.add_argument("--table", required=True)
    p_exp.add_argument("--csv", required=True)

    p_tab = sub.add_parser("tables", help="list tables in a DuckDB file")
    p_tab.add_argument("path")


def import_duckdb():
    try:
        import duckdb
    except ImportError as e:
        raise stwError(
            "E_NO_DUCKDB", "duckdb is not installed", "Run `pip install duckdb`."
        ) from e
    return duckdb


def guard_not_index(ws, rel: str) -> None:
    if ws.abs(rel) == ws.index_path:
        raise stwError(
            "E_FORBIDDEN",
            "stw's own index is not addressable as a DuckDB artifact",
            "Point at a workspace .db artifact instead.",
        )


def refresh_tables(ws, conn, rel: str, abspath: Path) -> None:
    """Recompute the `tables` rows for one artifact and reindex its files row
    so it shows up in the map with its table list (plan.md §4)."""
    duckdb = import_duckdb()
    dconn = duckdb.connect(str(abspath), read_only=True)
    try:
        names = [
            r[0]
            for r in dconn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
        ]
        rows = []
        for name in names:
            cnt = dconn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            cols = [
                {"name": c[1], "type": c[2]}
                for c in dconn.execute(f'PRAGMA table_info("{name}")').fetchall()
            ]
            rows.append((rel, name, cnt, jsonlib.dumps(cols)))
    finally:
        dconn.close()

    with tx(conn):
        conn.execute("DELETE FROM tables WHERE db_path = ?", (rel,))
        conn.executemany(
            "INSERT INTO tables(db_path, table_name, row_count, columns) VALUES(?,?,?,?)", rows
        )
    reindex(ws, conn, rel)


def run(ws, conn, args) -> int:
    duckdb = import_duckdb()

    rel = ws.rel(args.path)
    guard_not_index(ws, rel)
    abspath = ws.abs(rel)

    if args.subcmd == "new":
        if abspath.exists():
            raise stwError("E_EXISTS", f"{rel} already exists", "Use a different path.")
        abspath.parent.mkdir(parents=True, exist_ok=True)
        duckdb.connect(str(abspath)).close()
        refresh_tables(ws, conn, rel, abspath)
        out.emit(f"created {rel}", {"path": rel})
        return 0

    if not abspath.exists():
        raise stwError("E_NOT_FOUND", f"{rel} is not a DuckDB file", "Run `stw db new` first.")

    if args.subcmd == "import":
        csv_path = Path(args.csv)
        if not csv_path.is_absolute():
            csv_path = Path.cwd() / csv_path
        if not csv_path.exists():
            raise stwError("E_NOT_FOUND", f"{args.csv} does not exist")
        dconn = duckdb.connect(str(abspath))
        try:
            dconn.execute(
                f'CREATE OR REPLACE TABLE "{args.table}" AS SELECT * FROM read_csv_auto(?)',
                [str(csv_path)],
            )
            n = dconn.execute(f'SELECT COUNT(*) FROM "{args.table}"').fetchone()[0]
        finally:
            dconn.close()
        refresh_tables(ws, conn, rel, abspath)
        out.emit(
            f"imported {args.csv} -> {rel}::{args.table} · {n} rows",
            {"path": rel, "table": args.table, "rows": n},
        )
        return 0

    if args.subcmd == "export":
        dconn = duckdb.connect(str(abspath), read_only=True)
        try:
            dconn.execute(
                f'COPY (SELECT * FROM "{args.table}") TO ? (HEADER, DELIMITER \',\')',
                [args.csv],
            )
        finally:
            dconn.close()
        out.emit(
            f"exported {rel}::{args.table} -> {args.csv}",
            {"path": rel, "table": args.table, "csv": args.csv},
        )
        return 0

    if args.subcmd == "tables":
        refresh_tables(ws, conn, rel, abspath)
        rows = conn.execute(
            "SELECT table_name, row_count, columns FROM tables WHERE db_path = ? ORDER BY table_name",
            (rel,),
        ).fetchall()
        data = [
            {"table": r["table_name"], "rows": r["row_count"], "columns": jsonlib.loads(r["columns"])}
            for r in rows
        ]
        lines = [f"{d['table']:<20} {d['rows']:>8} rows" for d in data]
        out.emit("\n".join(lines) if lines else "no tables", {"path": rel, "tables": data})
        return 0

    raise stwError("E_USAGE", f"unknown db subcommand {args.subcmd!r}")
