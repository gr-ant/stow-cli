"""Tests for the content-addressed history store and log/undo/restore/gc/mv/rm
(plan.md §3 'History').
"""

from __future__ import annotations

import json
import zlib

from stow import history
from stow.db import connect, tx
from stow.workspace import Workspace


def _versions(ws_dir, rel):
    ws = Workspace.at(ws_dir)
    conn = connect(ws.index_path)
    try:
        return history.versions(conn, rel)
    finally:
        conn.close()


# --------------------------------------------------------------------------
# history.py directly
# --------------------------------------------------------------------------
def test_snapshot_writes_compressed_object_and_version_row(ws_dir):
    ws = Workspace.at(ws_dir)
    conn = connect(ws.index_path)
    (ws.root / "a.md").write_text("hello\n")
    with tx(conn):
        sha = history.snapshot(ws, conn, "a.md", "test")
    assert sha is not None

    obj = ws.objects_dir / sha[:2] / sha[2:]
    assert obj.exists()
    assert zlib.decompress(obj.read_bytes()) == b"hello\n"

    rows = history.versions(conn, "a.md")
    assert len(rows) == 1
    assert rows[0]["sha"] == sha
    assert rows[0]["command"] == "test"
    assert rows[0]["size"] == 6
    conn.close()


def test_snapshot_missing_file_is_noop(ws_dir):
    ws = Workspace.at(ws_dir)
    conn = connect(ws.index_path)
    with tx(conn):
        sha = history.snapshot(ws, conn, "nope.md", "test")
    assert sha is None
    assert history.versions(conn, "nope.md") == []
    conn.close()


def test_snapshot_never_duplicates_identical_sha_in_a_row(ws_dir):
    ws = Workspace.at(ws_dir)
    conn = connect(ws.index_path)
    (ws.root / "a.md").write_text("same\n")
    with tx(conn):
        sha1 = history.snapshot(ws, conn, "a.md", "one")
        sha2 = history.snapshot(ws, conn, "a.md", "two")  # content unchanged
    assert sha1 == sha2
    assert len(history.versions(conn, "a.md")) == 1
    conn.close()


def test_snapshot_records_a_repeat_sha_if_not_immediately_prior(ws_dir):
    ws = Workspace.at(ws_dir)
    conn = connect(ws.index_path)
    (ws.root / "a.md").write_text("v1\n")
    with tx(conn):
        history.snapshot(ws, conn, "a.md", "one")
    (ws.root / "a.md").write_text("v2\n")
    with tx(conn):
        history.snapshot(ws, conn, "a.md", "two")
    (ws.root / "a.md").write_text("v1\n")  # back to v1's content
    with tx(conn):
        history.snapshot(ws, conn, "a.md", "three")
    # not consecutive, so all three get recorded even though two shas repeat
    assert len(history.versions(conn, "a.md")) == 3
    conn.close()


def test_read_version_roundtrips(ws_dir):
    ws = Workspace.at(ws_dir)
    conn = connect(ws.index_path)
    (ws.root / "a.md").write_text("payload\n")
    with tx(conn):
        sha = history.snapshot(ws, conn, "a.md", "test")
    assert history.read_version(ws, sha) == b"payload\n"
    conn.close()


def test_prune_drops_old_versions_and_unreferenced_objects(ws_dir):
    ws = Workspace.at(ws_dir)
    conn = connect(ws.index_path)
    for i in range(3):
        (ws.root / "a.md").write_text(f"v{i}\n")
        with tx(conn):
            history.snapshot(ws, conn, "a.md", "step")
    assert len(history.versions(conn, "a.md")) == 3

    with tx(conn):
        stats = history.prune(ws, conn, keep=1)
    assert stats["versions_pruned"] == 2
    assert stats["objects_deleted"] == 2
    assert len(history.versions(conn, "a.md")) == 1
    conn.close()


def test_history_disabled_skips_snapshot(tmp_path, monkeypatch):
    from tests.conftest import run

    monkeypatch.chdir(tmp_path)
    run("init")
    (tmp_path / ".stow" / "config.toml").write_text("[history]\nenabled = false\n")

    ws = Workspace.at(tmp_path)
    conn = connect(ws.index_path)
    (ws.root / "a.md").write_text("hello\n")
    with tx(conn):
        sha = history.snapshot(ws, conn, "a.md", "test")
    assert sha is None
    assert history.versions(conn, "a.md") == []
    conn.close()


# --------------------------------------------------------------------------
# stw log
# --------------------------------------------------------------------------
def test_log_no_history(cli):
    cli("write", "a.md", stdin="v1\n")
    r = cli("log", "a.md")
    assert r.returncode == 0
    assert "no history" in r.stdout


def test_log_human_and_json(cli):
    cli("write", "a.md", stdin="v1\n")
    cli("write", "a.md", stdin="v2\n")
    r = cli("log", "a.md")
    assert r.returncode == 0
    assert "a.md" in r.stdout
    assert "write" in r.stdout

    rj = cli("--json", "log", "a.md")
    data = json.loads(rj.stdout)
    assert len(data["versions"]) == 1
    assert data["versions"][0]["command"] == "write"
    assert data["versions"][0]["size"] > 0


# --------------------------------------------------------------------------
# stw undo
# --------------------------------------------------------------------------
def test_undo_restores_previous_and_is_itself_undoable(cli, ws_dir):
    cli("write", "a.md", stdin="v1\n")
    cli("write", "a.md", stdin="v2\n")

    r = cli("undo", "a.md")
    assert r.returncode == 0
    assert "v1" in (ws_dir / "a.md").read_text()

    r2 = cli("undo", "a.md")
    assert r2.returncode == 0
    assert "v2" in (ws_dir / "a.md").read_text()


def test_undo_recreates_a_removed_file(cli, ws_dir):
    cli("write", "a.md", stdin="original\n")
    cli("rm", "a.md")
    assert not (ws_dir / "a.md").exists()

    r = cli("undo", "a.md")
    assert r.returncode == 0
    assert (ws_dir / "a.md").exists()
    assert "original" in (ws_dir / "a.md").read_text()


def test_undo_no_history_errors(cli):
    r = cli("undo", "never-existed.md")
    assert r.returncode != 0


# --------------------------------------------------------------------------
# stw restore
# --------------------------------------------------------------------------
def test_restore_by_at_sha_and_by_flag(cli, ws_dir):
    cli("write", "a.md", stdin="v1\n")
    cli("write", "a.md", stdin="v2\n")
    sha = _versions(ws_dir, "a.md")[0]["sha"]

    r = cli("restore", f"a.md@{sha[:8]}")
    assert r.returncode == 0
    assert "v1" in (ws_dir / "a.md").read_text()

    cli("write", "a.md", stdin="v3\n")
    r2 = cli("restore", "a.md", "--sha", sha[:8])
    assert r2.returncode == 0
    assert "v1" in (ws_dir / "a.md").read_text()


def test_restore_requires_a_version(cli):
    cli("write", "a.md", stdin="v1\n")
    cli("write", "a.md", stdin="v2\n")
    r = cli("restore", "a.md")
    assert r.returncode != 0


def test_restore_unknown_sha_errors(cli):
    cli("write", "a.md", stdin="v1\n")
    cli("write", "a.md", stdin="v2\n")
    r = cli("restore", "a.md", "--sha", "ffffffff")
    assert r.returncode != 0
    assert "E_NOT_FOUND" in r.stderr


def test_restore_ambiguous_prefix_errors(cli, ws_dir):
    cli("write", "a.md", stdin="v1\n")
    ws = Workspace.at(ws_dir)
    conn = connect(ws.index_path)
    with tx(conn):
        conn.execute(
            "INSERT INTO versions(sha, path, size, command, created_at) VALUES(?,?,?,?,?)",
            ("aaaa1111" + "0" * 56, "a.md", 2, "test", "2026-01-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO versions(sha, path, size, command, created_at) VALUES(?,?,?,?,?)",
            ("aaaa2222" + "0" * 56, "a.md", 2, "test", "2026-01-01T00:00:01Z"),
        )
    conn.close()

    r = cli("restore", "a.md", "--sha", "aaaa")
    assert r.returncode != 0
    assert "E_AMBIGUOUS_SHA" in r.stderr


# --------------------------------------------------------------------------
# stw mv
# --------------------------------------------------------------------------
def test_mv_moves_file_and_rewrites_links(cli, ws_dir):
    cli("new", "a/target.md", "--about", "t", "--tags", "t", stdin="## Sect\nbody\n")
    cli("new", "a/src.md", "--about", "s", "--tags", "s",
        stdin="see [[target]] and [link](a/target.md#sect) here\n")

    r = cli("mv", "a/target.md", "b/target.md")
    assert r.returncode == 0
    assert not (ws_dir / "a/target.md").exists()
    assert (ws_dir / "b/target.md").exists()

    text = (ws_dir / "a/src.md").read_text()
    assert "b/target" in text
    assert "a/target.md" not in text


def test_mv_reports_rewritten_count_in_json(cli, ws_dir):
    cli("new", "a/target.md", "--about", "t", "--tags", "t", stdin="## Sect\nbody\n")
    cli("new", "a/src.md", "--about", "s", "--tags", "s",
        stdin="see [[target]] and [link](a/target.md#sect) here\n")
    r = cli("--json", "mv", "a/target.md", "b/target.md")
    data = json.loads(r.stdout)
    assert data["rewritten"] == 2
    assert data["files_touched"] == ["a/src.md"]


def test_mv_into_existing_directory(cli, ws_dir):
    cli("write", "docs/x.md", stdin="hi\n")
    (ws_dir / "archive").mkdir()
    r = cli("mv", "docs/x.md", "archive")
    assert r.returncode == 0
    assert (ws_dir / "archive/x.md").exists()


def test_mv_refuses_overwrite_without_force(cli, ws_dir):
    cli("write", "a.md", stdin="a\n")
    cli("write", "b.md", stdin="b\n")
    r = cli("mv", "a.md", "b.md")
    assert r.returncode != 0
    assert "E_EXISTS" in r.stderr
    assert (ws_dir / "a.md").exists()

    r2 = cli("mv", "a.md", "b.md", "--force")
    assert r2.returncode == 0
    assert not (ws_dir / "a.md").exists()


def test_mv_snapshots_before_moving_and_carries_history_to_the_new_path(cli, ws_dir):
    """History is keyed by path, so `mv` re-keys it.

    Left under the old path it would be unreachable: `stw log b.md` would report
    nothing seconds after the agent wrote the file.
    """
    cli("write", "a.md", stdin="content\n")
    cli("mv", "a.md", "b.md")
    assert _versions(ws_dir, "a.md") == []
    rows = _versions(ws_dir, "b.md")
    assert len(rows) == 1
    assert rows[0]["command"] == "mv"


# --------------------------------------------------------------------------
# stw rm
# --------------------------------------------------------------------------
def test_rm_refuses_with_backlinks_names_sources(cli):
    cli("new", "a/target.md", "--about", "t", "--tags", "t", stdin="content here\n")
    cli("new", "a/src.md", "--about", "s", "--tags", "s", stdin="see [[target]]\n")

    r = cli("rm", "a/target.md")
    assert r.returncode != 0
    assert "E_BACKLINKS" in r.stderr
    assert "a/src.md" in r.stderr


def test_rm_force_removes_and_is_restorable(cli, ws_dir):
    cli("new", "a/target.md", "--about", "t", "--tags", "t", stdin="content here\n")
    cli("new", "a/src.md", "--about", "s", "--tags", "s", stdin="see [[target]]\n")

    r = cli("rm", "a/target.md", "--force")
    assert r.returncode == 0
    assert not (ws_dir / "a/target.md").exists()

    sha = _versions(ws_dir, "a/target.md")[0]["sha"]
    r2 = cli("restore", f"a/target.md@{sha[:8]}")
    assert r2.returncode == 0
    assert (ws_dir / "a/target.md").exists()
    assert "content here" in (ws_dir / "a/target.md").read_text()


def test_rm_missing_file_errors(cli):
    r = cli("rm", "nope.md")
    assert r.returncode != 0
    assert "E_NOT_FOUND" in r.stderr


# --------------------------------------------------------------------------
# stw gc
# --------------------------------------------------------------------------
def test_gc_prunes_history_and_drops_orphaned_embeddings(cli, ws_dir):
    for i in range(6):
        assert cli("write", "a.md", stdin=f"version {i}\n").returncode == 0
    assert len(_versions(ws_dir, "a.md")) == 5

    ws = Workspace.at(ws_dir)
    conn = connect(ws.index_path)
    with tx(conn):
        conn.execute(
            "INSERT INTO embeddings(embed_sha, model, dim, vec) VALUES(?,?,?,?)",
            ("orphan" + "0" * 58, "test-model", 4, b"\x00" * 16),
        )
    conn.close()

    r = cli("--json", "gc", "--keep", "2")
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["versions_pruned"] == 3
    assert data["embeddings_dropped"] == 1
    assert len(_versions(ws_dir, "a.md")) == 2
