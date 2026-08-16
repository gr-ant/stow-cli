"""Tests for `stw read` and `stw outline` (plan.md §8, §10)."""

from __future__ import annotations

import json

from helpers import seed

RAG = """---
title: RAG
about: retrieval strategies
tags: [research]
---

# RAG

## Chunking

Some text about chunking.

### Overlap

roughly 15% overlap preserves cross-boundary context.

## Evaluation

Eval notes.
"""


def test_read_whole_file(cli, ws_dir):
    seed(ws_dir, {"research/rag.md": RAG})
    r = cli("read", "research/rag.md")
    assert r.returncode == 0
    assert r.stdout == RAG


def test_read_section(cli, ws_dir):
    seed(ws_dir, {"research/rag.md": RAG})
    r = cli("read", "research/rag.md", "--section", "Chunking/Overlap")
    assert r.returncode == 0
    assert r.stdout.startswith("### Overlap")
    assert "roughly 15% overlap" in r.stdout
    assert "Evaluation" not in r.stdout


def test_read_section_via_address_form(cli, ws_dir):
    seed(ws_dir, {"research/rag.md": RAG})
    r = cli("read", "research/rag.md#Chunking/Overlap")
    assert r.returncode == 0
    assert r.stdout.startswith("### Overlap")


def test_read_lines(cli, ws_dir):
    seed(ws_dir, {"research/rag.md": RAG})
    r = cli("read", "research/rag.md", "--lines", "1:3")
    assert r.returncode == 0
    lines = r.stdout.split("\n")
    assert lines[0] == "---"
    assert lines[1] == "title: RAG"
    assert lines[2] == "about: retrieval strategies"
    assert len(lines) == 4  # trailing newline


def test_read_head(cli, ws_dir):
    seed(ws_dir, {"research/rag.md": RAG})
    r = cli("read", "research/rag.md", "--head", "1")
    assert r.stdout.strip() == "---"


def test_read_json_mode(cli, ws_dir):
    seed(ws_dir, {"research/rag.md": RAG})
    r = cli("--json", "read", "research/rag.md", "--section", "Chunking")
    data = json.loads(r.stdout)
    assert data["path"] == "research/rag.md"
    assert data["section"] == "Chunking"
    assert "### Overlap" in data["content"]


def test_read_missing_section_errors(cli, ws_dir):
    seed(ws_dir, {"research/rag.md": RAG})
    r = cli("read", "research/rag.md", "--section", "Nope")
    assert r.returncode != 0
    assert "E_NO_SUCH_HEADING" in r.stderr


def test_read_ambiguous_section_errors(cli, ws_dir):
    dup = "# T\n\n## Overlap\n\nfirst\n\n## Other\n\n## Overlap\n\nsecond\n"
    seed(ws_dir, {"notes/dup.md": dup})
    r = cli("read", "notes/dup.md", "--section", "Overlap")
    assert r.returncode != 0
    assert "E_AMBIGUOUS_HEADING" in r.stderr


def test_read_missing_file_errors(cli, ws_dir):
    r = cli("read", "nope.md")
    assert r.returncode != 0
    assert "E_NOT_FOUND" in r.stderr


def test_read_stale_warns_but_answers(cli, ws_dir):
    seed(ws_dir, {"notes/x.md": "# X\n\nbody\n"})
    (ws_dir / "notes" / "x.md").write_text("# X\n\nchanged body with more bytes\n")
    r = cli("read", "notes/x.md")
    assert r.returncode == 0
    assert "W_STALE" in r.stderr
    assert "changed body" in r.stdout


def test_outline_shows_tree_and_lines(cli, ws_dir):
    seed(ws_dir, {"research/rag.md": RAG})
    r = cli("outline", "research/rag.md")
    assert r.returncode == 0
    assert "# RAG" in r.stdout
    assert "## Chunking" in r.stdout
    assert "### Overlap" in r.stdout
    assert "L" in r.stdout  # line numbers present


def test_outline_sha_flag_adds_hashes(cli, ws_dir):
    seed(ws_dir, {"research/rag.md": RAG})
    without = cli("outline", "research/rag.md")
    with_sha = cli("outline", "research/rag.md", "--sha")
    assert len(with_sha.stdout) > len(without.stdout)


def test_outline_json_has_content_sha(cli, ws_dir):
    seed(ws_dir, {"research/rag.md": RAG})
    r = cli("--json", "outline", "research/rag.md", "--sha")
    data = json.loads(r.stdout)
    heads = {h["heading_path"]: h for h in data["headings"]}
    assert heads["Chunking/Overlap"]["content_sha"]
    assert len(heads["Chunking/Overlap"]["content_sha"]) == 64
