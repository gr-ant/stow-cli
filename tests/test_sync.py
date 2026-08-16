"""Tests for `stw sync` (plan.md §12).

Generated files (map.md, AGENTS.md, CLAUDE.md) are excluded from the index by
default, so counts here cover only the seeded content.
"""

from __future__ import annotations

import argparse
import pathlib
import time

from helpers import seed

from stow.commands import sync as sync_cmd
from stow.db import connect


def _args(force=False, prune=True, json=False):
    return argparse.Namespace(force=force, prune=prune, json=json)


def test_sync_untouched_workspace_reports_nothing_and_reads_no_content(ws_dir, monkeypatch):
    ws = seed(ws_dir, {"a.md": "# A\n", "notes/b.md": "# B\n", "notes/c.md": "# C\n"})

    conn = connect(ws.index_path)
    try:
        calls = {"n": 0}
        orig_read_bytes = pathlib.Path.read_bytes

        def counting_read_bytes(self, *a, **kw):
            calls["n"] += 1
            return orig_read_bytes(self, *a, **kw)

        monkeypatch.setattr(pathlib.Path, "read_bytes", counting_read_bytes)

        rc = sync_cmd.run(ws, conn, _args())
    finally:
        conn.close()

    assert rc == 0
    assert calls["n"] == 0, "sync must not read file contents when nothing changed"


def test_sync_cli_reports_zero_on_untouched_workspace(cli, ws_dir):
    seed(ws_dir, {"a.md": "# A\n", "b.md": "# B\n"})
    r = cli("sync")
    assert r.returncode == 0
    assert "0 changed" in r.stdout
    assert "0 new" in r.stdout
    assert "0 removed" in r.stdout
    assert "2 unchanged" in r.stdout  # a.md, b.md -- AGENTS.md is a generated file, excluded


def test_sync_detects_changed_content(cli, ws_dir):
    seed(ws_dir, {"a.md": "# A\n\noriginal\n"})
    cli("sync")
    time.sleep(0.01)
    (ws_dir / "a.md").write_text("# A\n\nrewritten with more content than before\n")
    r = cli("sync")
    assert r.returncode == 0
    assert "1 changed" in r.stdout


def test_sync_detects_new_file(cli, ws_dir):
    seed(ws_dir, {"a.md": "# A\n"})
    (ws_dir / "b.md").write_text("# B\n\nnew file added directly on disk\n")
    r = cli("sync")
    assert r.returncode == 0
    assert "1 new" in r.stdout

    r2 = cli("ls")
    assert "b.md" in r2.stdout


def test_sync_prune_removes_missing_file(cli, ws_dir):
    seed(ws_dir, {"a.md": "# A\n", "b.md": "# B\n"})
    (ws_dir / "b.md").unlink()
    r = cli("sync")
    assert r.returncode == 0
    assert "1 removed" in r.stdout

    r2 = cli("ls")
    assert "b.md" not in r2.stdout


def test_sync_no_prune_keeps_registry_row(cli, ws_dir):
    seed(ws_dir, {"a.md": "# A\n", "b.md": "# B\n"})
    (ws_dir / "b.md").unlink()
    r = cli("sync", "--no-prune")
    assert r.returncode == 0
    assert "0 removed" in r.stdout

    r2 = cli("ls")
    assert "b.md" in r2.stdout  # row survives, even though the file is gone


def test_sync_force_rehashes_even_without_stat_change(cli, ws_dir):
    seed(ws_dir, {"a.md": "# A\n\nbody\n"})
    r = cli("sync", "--force")
    assert r.returncode == 0
    # nothing actually changed, so force still reports it as unchanged
    assert "0 changed" in r.stdout
    assert "1 unchanged" in r.stdout  # a.md


def test_sync_json_shape(cli, ws_dir):
    import json

    seed(ws_dir, {"a.md": "# A\n"})
    r = cli("--json", "sync")
    data = json.loads(r.stdout)
    assert set(data) == {"changed", "new", "removed", "unchanged"}
