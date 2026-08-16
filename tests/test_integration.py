"""Cross-command integration.

These cover the seams between the write surface, the graph, and retrieval —
where the per-command unit tests each saw only their own half and the bug lived
in the gap.
"""

from __future__ import annotations

import json

from stow.db import connect


def test_mv_leaves_no_trace_of_the_old_path(cli, ws_dir):
    """A moved file must not appear twice in the registry.

    `mv` moved the bytes and reindexed the destination but never dropped the
    source row, so `map` and `ls` both listed the file at its old and new path,
    and every count in the map header was inflated.
    """
    cli("new", "research/rag.md", "--about", "retrieval strategies")
    cli("mv", "research/rag.md", "notes/rag.md")

    r = cli("ls")
    assert "notes/rag.md" in r.stdout
    assert "research/rag.md" not in r.stdout

    r = cli("map", "--json")
    payload = json.loads(r.stdout)
    paths = json.dumps(payload)
    assert "research/rag.md" not in paths

    ws_conn = connect(ws_dir / ".stow" / "stow.db")
    try:
        rows = [r["path"] for r in ws_conn.execute("SELECT path FROM files")]
        assert rows == ["notes/rag.md"]
        # headings/links/chunks are FK-cascaded, but check one explicitly
        orphans = ws_conn.execute(
            "SELECT count(*) c FROM chunks WHERE path = 'research/rag.md'"
        ).fetchone()["c"]
        assert orphans == 0
    finally:
        ws_conn.close()


def test_history_follows_a_moved_file(cli, ws_dir):
    """`log`/`undo` must keep working across a `mv`.

    Versions are keyed by path, so a move used to orphan the whole history:
    seconds after writing a file the agent would be told there was nothing to
    undo.
    """
    cli("new", "a.md", "--about", "x")
    cli("write", "a.md", stdin="# A\n\nfirst body\n")
    cli("write", "a.md", stdin="# A\n\nsecond body\n")
    cli("mv", "a.md", "b.md")

    r = cli("log", "b.md", "--json")
    versions = json.loads(r.stdout)["versions"]
    assert len(versions) >= 2

    r = cli("undo", "b.md")
    assert r.returncode == 0
    assert "second body" in (ws_dir / "b.md").read_text()


def test_find_points_at_the_section_the_text_is_actually_in(cli, ws_dir):
    """The location `find` prints must match where the chunk starts.

    Short sections merged forward across parent boundaries and inherited the
    LAST section's heading path, so a hit on text under `Chunking/Overlap` was
    reported as living under `Evaluation`.
    """
    cli(
        "write",
        "rag.md",
        stdin=(
            "# RAG\n\n## Chunking\n\n### Overlap\n\n"
            "About 15 percent overlap preserves cross-boundary context.\n\n"
            "### Sizing\n\nTarget 1200 characters per chunk.\n\n"
            "## Evaluation\n\nrecall@5 is the metric we track.\n"
        ),
    )
    r = cli("find", "overlap", "-k", "3", "--text-only", "--json")
    hits = json.loads(r.stdout)["hits"]
    assert hits, "expected a hit for 'overlap'"
    top = hits[0]
    assert top["heading_path"].startswith("Chunking")
    assert "overlap" in top["text"].lower()


def test_set_then_find_reflects_the_edit(cli, ws_dir):
    """A section edit must reach the search index without an explicit sync."""
    cli(
        "write",
        "rag.md",
        stdin="# RAG\n\n## Chunking\n\nold text about widgets and sprockets here.\n",
    )
    cli("set", "rag.md#Chunking", stdin="new text about parsnips and turnips instead.\n")

    r = cli("find", "parsnips", "--text-only", "--json")
    hits = json.loads(r.stdout)["hits"]
    assert hits and "parsnips" in hits[0]["text"]

    r = cli("find", "sprockets", "--text-only", "--json")
    assert json.loads(r.stdout)["hits"] == []


def test_rm_then_undo_round_trips_through_the_index(cli, ws_dir):
    cli("write", "a.md", stdin="# A\n\nbody text that is long enough to index.\n")
    cli("rm", "a.md")
    assert not (ws_dir / "a.md").exists()

    r = cli("ls")
    assert "a.md" not in r.stdout

    cli("undo", "a.md")
    assert (ws_dir / "a.md").exists()
    r = cli("ls")
    assert "a.md" in r.stdout
