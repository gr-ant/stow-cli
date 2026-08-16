"""DuckDB artifacts (plan.md §4)."""

from __future__ import annotations

import json


def _write_csv(path, header, rows) -> None:
    lines = [header] + [",".join(str(v) for v in row) for row in rows]
    path.write_text("\n".join(lines) + "\n")


# --------------------------------------------------------------------------
# stw db new/import/export/tables
# --------------------------------------------------------------------------
def test_db_new_creates_file_and_registers_it(ws_dir, cli):
    r = cli("db", "new", "data/exp.db")
    assert r.returncode == 0
    assert (ws_dir / "data" / "exp.db").exists()


def test_db_new_refuses_to_clobber_existing_file(ws_dir, cli):
    cli("db", "new", "data/exp.db")
    r = cli("db", "new", "data/exp.db")
    assert r.returncode != 0
    assert "E_EXISTS" in r.stderr


def test_db_import_and_tables(ws_dir, cli):
    cli("db", "new", "data/exp.db")
    csv = ws_dir / "runs.csv"
    _write_csv(csv, "id,cfg,score", [(1, "a", 0.5), (2, "b", 0.9)])

    r = cli("db", "import", "data/exp.db", "--csv", str(csv), "--as", "runs")
    assert r.returncode == 0

    r2 = cli("db", "tables", "data/exp.db", "--json")
    data = json.loads(r2.stdout)
    tables = {t["table"]: t for t in data["tables"]}
    assert tables["runs"]["rows"] == 2
    cols = {c["name"] for c in tables["runs"]["columns"]}
    assert {"id", "cfg", "score"} <= cols


def test_db_export_round_trips_csv(ws_dir, cli):
    cli("db", "new", "data/exp.db")
    csv_in = ws_dir / "runs.csv"
    _write_csv(csv_in, "id,val", [(1, 10), (2, 20)])
    cli("db", "import", "data/exp.db", "--csv", str(csv_in), "--as", "runs")

    csv_out = ws_dir / "out.csv"
    r = cli("db", "export", "data/exp.db", "--table", "runs", "--csv", str(csv_out))
    assert r.returncode == 0
    assert csv_out.exists()
    text = csv_out.read_text()
    assert "10" in text and "20" in text


def test_db_rejects_stow_index(ws_dir, cli):
    r = cli("db", "tables", ".stow/stow.db")
    assert r.returncode != 0
    assert "E_FORBIDDEN" in r.stderr


def test_db_tables_missing_file_errors(ws_dir, cli):
    r = cli("db", "tables", "data/nope.db")
    assert r.returncode != 0
    assert "E_NOT_FOUND" in r.stderr


def test_db_new_registers_a_files_row(ws_dir, cli):
    r = cli("db", "new", "data/exp.db")
    assert r.returncode == 0

    from stow.db import connect
    from stow.workspace import Workspace

    ws = Workspace.at(ws_dir)
    conn = connect(ws.index_path)
    try:
        row = conn.execute("SELECT kind FROM files WHERE path = ?", ("data/exp.db",)).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["kind"] == "db"


# --------------------------------------------------------------------------
# stw sql
# --------------------------------------------------------------------------
def test_sql_select_returns_rows(ws_dir, cli):
    cli("db", "new", "data/exp.db")
    csv = ws_dir / "runs.csv"
    _write_csv(csv, "id,cfg,score", [(1, "a", 0.5), (2, "b", 0.9)])
    cli("db", "import", "data/exp.db", "--csv", str(csv), "--as", "runs")

    r = cli("sql", "data/exp.db", "SELECT * FROM runs ORDER BY id")
    assert r.returncode == 0
    assert "id | cfg | score" in r.stdout
    assert "1 | a | 0.5" in r.stdout


def test_sql_default_limit_applied_and_footer_names_total(ws_dir, cli):
    cli("db", "new", "data/big.db")
    r = cli("sql", "data/big.db", "CREATE TABLE t AS SELECT range AS id FROM range(150)")
    assert r.returncode == 0

    r2 = cli("sql", "data/big.db", "SELECT * FROM t")
    assert r2.returncode == 0
    lines = r2.stdout.strip().splitlines()
    # header + 100 data rows + footer
    assert len(lines) == 102
    assert "LIMIT 100" in lines[-1]
    assert "150" in lines[-1]


def test_sql_explicit_limit_overrides_default(ws_dir, cli):
    cli("db", "new", "data/big2.db")
    cli("sql", "data/big2.db", "CREATE TABLE t AS SELECT range AS id FROM range(150)")

    r = cli("sql", "data/big2.db", "SELECT * FROM t", "--limit", "5")
    lines = r.stdout.strip().splitlines()
    assert len(lines) == 1 + 5 + 1
    assert "LIMIT 5" in lines[-1]


def test_sql_select_with_explicit_limit_in_query_is_not_double_limited(ws_dir, cli):
    cli("db", "new", "data/small.db")
    cli("sql", "data/small.db", "CREATE TABLE t AS SELECT range AS id FROM range(10)")
    r = cli("sql", "data/small.db", "SELECT * FROM t LIMIT 3")
    lines = r.stdout.strip().splitlines()
    assert len(lines) == 1 + 3  # header + 3 rows, no footer (already had its own LIMIT)


def test_sql_ddl_refreshes_tables_registry(ws_dir, cli):
    cli("db", "new", "data/exp2.db")
    r = cli("sql", "data/exp2.db", "CREATE TABLE runs(id INT, cfg TEXT, score DOUBLE)")
    assert r.returncode == 0

    r2 = cli("db", "tables", "data/exp2.db", "--json")
    data = json.loads(r2.stdout)
    names = [t["table"] for t in data["tables"]]
    assert "runs" in names


def test_sql_rejects_stow_index(ws_dir, cli):
    r = cli("sql", ".stow/stow.db", "SELECT 1")
    assert r.returncode != 0
    assert "E_FORBIDDEN" in r.stderr


def test_sql_missing_file_errors(ws_dir, cli):
    r = cli("sql", "data/nope.db", "SELECT 1")
    assert r.returncode != 0
    assert "E_NOT_FOUND" in r.stderr


# --------------------------------------------------------------------------
# lazy import: duckdb must not be required for commands that don't need it
# --------------------------------------------------------------------------
def test_duckdb_is_not_imported_at_module_load(ws_dir, cli, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "duckdb", None)
    try:
        # Importing the command modules themselves must not require duckdb.
        import importlib

        for name in ("stow.commands.db", "stow.commands.sql"):
            sys.modules.pop(name, None)
            importlib.import_module(name)
    finally:
        sys.modules.pop("duckdb", None)


def test_missing_duckdb_raises_e_no_duckdb(monkeypatch):
    import sys

    from stow.commands import db as db_cmd

    monkeypatch.setitem(sys.modules, "duckdb", None)
    try:
        try:
            db_cmd.import_duckdb()
            assert False, "expected E_NO_DUCKDB"
        except Exception as e:
            assert "E_NO_DUCKDB" in str(e)
    finally:
        sys.modules.pop("duckdb", None)
