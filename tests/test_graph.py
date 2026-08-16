"""Tests for `stw links`, `stw backlinks`, `stw doctor` (plan.md §9)."""

from __future__ import annotations

import json

from helpers import seed

from stow.db import connect
from stow.index import reresolve_incoming
from stow.workspace import Workspace


def _resolve_all(root) -> None:
    """seed() indexes files in dict order, so a link to a file seeded later in
    a different directory starts out unresolved (disk-existence probing only
    checks the source's own directory and the workspace root). Settle it the
    same way `stw new`/`stw mv` would in real use."""
    ws = Workspace.at(root)
    conn = connect(ws.index_path)
    try:
        reresolve_incoming(ws, conn)
    finally:
        conn.close()


def test_links_outbound_resolved_and_unresolved(cli, ws_dir):
    seed(
        ws_dir,
        {
            "research/rag.md": "# RAG\n\nSee [[eval-log]] and [[nowhere]].\n",
            "notes/eval-log.md": "# Log\n",
        },
    )
    _resolve_all(ws_dir)
    r = cli("links", "research/rag.md")
    assert r.returncode == 0
    assert "notes/eval-log.md" in r.stdout
    assert "UNRESOLVED" in r.stdout
    assert "[[eval-log]]" in r.stdout
    assert "[[nowhere]]" in r.stdout


def test_links_json_reports_resolved_flag(cli, ws_dir):
    seed(
        ws_dir,
        {"a.md": "# A\n\n[[b]]\n[[nowhere]]\n", "b.md": "# B\n"},
    )
    r = cli("--json", "links", "a.md")
    data = json.loads(r.stdout)
    by_raw = {d["raw"]: d for d in data["links"]}
    assert by_raw["[[b]]"]["resolved"] is True
    assert by_raw["[[b]]"]["target_path"] == "b.md"
    assert by_raw["[[nowhere]]"]["resolved"] is False


def test_links_no_outbound(cli, ws_dir):
    seed(ws_dir, {"a.md": "# A\n\nno links here\n"})
    r = cli("links", "a.md")
    assert r.returncode == 0
    assert "no outbound links" in r.stdout


def test_backlinks_shows_inbound(cli, ws_dir):
    seed(
        ws_dir,
        {
            "research/rag.md": "# RAG\n\nSee [[eval-log]].\n",
            "notes/eval-log.md": "# Log\n",
        },
    )
    _resolve_all(ws_dir)
    r = cli("backlinks", "notes/eval-log.md")
    assert r.returncode == 0
    assert "research/rag.md:3" in r.stdout
    assert "[[eval-log]]" in r.stdout


def test_backlinks_none(cli, ws_dir):
    seed(ws_dir, {"a.md": "# A\n"})
    r = cli("backlinks", "a.md")
    assert r.returncode == 0
    assert "no backlinks" in r.stdout


def test_backlinks_excludes_self_links(cli, ws_dir):
    seed(ws_dir, {"a.md": "# A\n\n[[a]]\n"})
    r = cli("backlinks", "a.md")
    assert "no backlinks" in r.stdout


def test_doctor_clean_when_nothing_wrong(cli, ws_dir):
    ws = seed(ws_dir, {"a.md": "# A\n\n[[b]]\n", "b.md": "# B\n\n[[a]]\n"})
    # Fresh chunks are dirty by construction (embeddings are deferred, plan.md
    # §6) -- clear that flag here so this test isolates the "clean" reporting
    # path from the always-on dirty-chunk state.
    conn = connect(ws.index_path)
    try:
        conn.execute("UPDATE chunks SET dirty = 0")
    finally:
        conn.close()
    r = cli("doctor")
    assert r.returncode == 0
    assert "clean" in r.stdout


def test_doctor_reports_broken_link(cli, ws_dir):
    seed(ws_dir, {"a.md": "# A\n\n[[nowhere]]\n"})
    r = cli("doctor")
    assert r.returncode == 1
    assert "broken links" in r.stdout
    assert "[[nowhere]]" in r.stdout


def test_doctor_reports_orphan(cli, ws_dir):
    seed(ws_dir, {"a.md": "# A\n\nno links\n", "orphan.md": "# Orphan\n\nnothing links here\n"})
    r = cli("doctor")
    assert r.returncode == 1
    assert "orphans" in r.stdout
    assert "orphan.md" in r.stdout


def test_doctor_reports_duplicate_headings(cli, ws_dir):
    dup = "# T\n\n## Overlap\n\nfirst\n\n## Other\n\n## Overlap\n\nsecond\n"
    seed(ws_dir, {"notes/dup.md": dup})
    r = cli("doctor")
    assert r.returncode == 1
    assert "duplicate headings" in r.stdout
    assert "notes/dup.md#Overlap" in r.stdout


def test_doctor_reports_dirty_chunks(cli, ws_dir):
    seed(ws_dir, {"a.md": "# A\n\nsome body text\n"})
    r = cli("doctor")
    assert r.returncode == 1
    assert "dirty chunks" in r.stdout


def test_doctor_reports_ambiguous_wiki_target(cli, ws_dir):
    seed(
        ws_dir,
        {
            "a/overlap.md": "# A Overlap\n",
            "b/overlap.md": "# B Overlap\n",
            "c.md": "# C\n\nSee [[overlap]].\n",
        },
    )
    r = cli("doctor")
    assert r.returncode == 1
    assert "ambiguous wiki targets" in r.stdout
    assert "a/overlap.md" in r.stdout and "b/overlap.md" in r.stdout


def test_doctor_json_exit_code_and_shape(cli, ws_dir):
    seed(ws_dir, {"a.md": "# A\n\n[[nowhere]]\n"})
    r = cli("--json", "doctor")
    assert r.returncode == 1
    data = json.loads(r.stdout)
    assert data["total"] >= 1
    assert "broken_links" in data["categories"]


def test_doctor_all_flag_shows_more_than_default_cap(cli, ws_dir):
    files = {f"n{i}.md": f"# N{i}\n\n[[missing{i}]]\n" for i in range(15)}
    seed(ws_dir, files)
    capped = cli("doctor")
    full = cli("doctor", "--all")
    assert "more, run with --all" in capped.stdout
    assert "more, run with --all" not in full.stdout
    assert full.stdout.count("missing") >= 15
