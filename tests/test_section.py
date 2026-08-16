"""Tests for `stw set` — section addressing and replacement (plan.md §3).

This is the highest-value command in the tool (token savings from editing one
section instead of rewriting the file), so it gets exercised hard: nested
headings, a section at EOF, a section containing a fenced code block with
'## text' inside it, duplicate leaf names, and --expect-sha hit/miss.
"""

from __future__ import annotations

import json
import sqlite3

DOC = """# Title

## Chunking

Intro text.

### Overlap

~15% overlap works well.

```
## text inside fence, not a heading
```

More prose after fence.

## Evaluation

### Overlap

Different overlap section, duplicate leaf name.

## Last Section

Final content at EOF, no trailing heading after this.
"""


def _seed_doc(cli):
    r = cli("write", "docs/rag.md", stdin=DOC)
    assert r.returncode == 0


def _content_sha(ws_dir, path, heading_path):
    conn = sqlite3.connect(str(ws_dir / ".stow" / "stow.db"))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT content_sha FROM headings WHERE path = ? AND heading_path = ?",
        (path, heading_path),
    ).fetchone()
    conn.close()
    assert row is not None, f"no heading {heading_path!r} indexed for {path}"
    return row["content_sha"]


def test_set_replaces_nested_section(cli, ws_dir):
    _seed_doc(cli)
    r = cli("set", "docs/rag.md#Chunking/Overlap", stdin="new overlap body\n")
    assert r.returncode == 0
    text = (ws_dir / "docs/rag.md").read_text()
    assert "new overlap body" in text
    assert "~15% overlap works well." not in text
    assert "Final content at EOF" in text  # sibling section untouched


def test_set_section_containing_fenced_code_block_with_heading_text(cli, ws_dir):
    _seed_doc(cli)
    # The fence body contains '## text' — parse_headings must not treat it as
    # a heading boundary, so the whole fenced block belongs to this section.
    r = cli("set", "docs/rag.md#Chunking/Overlap", stdin="replaced\n")
    assert r.returncode == 0
    text = (ws_dir / "docs/rag.md").read_text()
    assert "text inside fence" not in text
    assert "More prose after fence." not in text
    assert "## Evaluation" in text  # next sibling untouched


def test_set_replaces_last_section_at_eof(cli, ws_dir):
    _seed_doc(cli)
    r = cli("set", "docs/rag.md#Last Section", stdin="brand new ending\n")
    assert r.returncode == 0
    text = (ws_dir / "docs/rag.md").read_text()
    assert text.rstrip().endswith("brand new ending")
    assert "Final content at EOF" not in text


def test_set_ambiguous_heading_by_leaf_name(cli, ws_dir):
    _seed_doc(cli)
    r = cli("set", "docs/rag.md#Overlap", stdin="x\n")
    assert r.returncode != 0
    assert "E_AMBIGUOUS_HEADING" in r.stderr
    assert "Overlap" in r.stderr


def test_set_full_path_disambiguates_duplicate_leaf(cli, ws_dir):
    _seed_doc(cli)
    r = cli("set", "docs/rag.md#Evaluation/Overlap", stdin="disambiguated body\n")
    assert r.returncode == 0
    text = (ws_dir / "docs/rag.md").read_text()
    assert "disambiguated body" in text
    assert "Different overlap section" not in text
    # the OTHER 'Overlap' section is untouched
    assert "~15% overlap works well." in text


def test_set_no_such_heading(cli, ws_dir):
    _seed_doc(cli)
    r = cli("set", "docs/rag.md#Nope", stdin="x\n")
    assert r.returncode != 0
    assert "E_NO_SUCH_HEADING" in r.stderr


def test_set_missing_address_errors(cli, ws_dir):
    _seed_doc(cli)
    r = cli("set", "docs/rag.md", stdin="x\n")
    assert r.returncode != 0


def test_set_missing_file_errors(cli):
    r = cli("set", "nope.md#Section", stdin="x\n")
    assert r.returncode != 0
    assert "E_NOT_FOUND" in r.stderr


def test_expect_sha_full_hit(cli, ws_dir):
    _seed_doc(cli)
    full = _content_sha(ws_dir, "docs/rag.md", "Chunking/Overlap")
    r = cli("set", "docs/rag.md#Chunking/Overlap", "--expect-sha", full, stdin="hit body\n")
    assert r.returncode == 0
    assert "hit body" in (ws_dir / "docs/rag.md").read_text()


def test_expect_sha_short_prefix_hit(cli, ws_dir):
    _seed_doc(cli)
    prefix = _content_sha(ws_dir, "docs/rag.md", "Evaluation/Overlap")[:8]
    r = cli("set", "docs/rag.md#Evaluation/Overlap", "--expect-sha", prefix, stdin="prefix hit\n")
    assert r.returncode == 0
    assert "prefix hit" in (ws_dir / "docs/rag.md").read_text()


def test_expect_sha_miss(cli, ws_dir):
    _seed_doc(cli)
    r = cli("set", "docs/rag.md#Chunking/Overlap", "--expect-sha", "deadbeef", stdin="x\n")
    assert r.returncode != 0
    assert "E_STALE_SECTION" in r.stderr
    assert "docs/rag.md#Chunking/Overlap" in r.stderr
    # nothing was written
    assert "~15% overlap works well." in (ws_dir / "docs/rag.md").read_text()


def test_set_body_opening_with_own_heading_replaces_heading_line(cli, ws_dir):
    _seed_doc(cli)
    r = cli("set", "docs/rag.md#Last Section", stdin="## Last Section\nfresh\n")
    assert r.returncode == 0
    text = (ws_dir / "docs/rag.md").read_text()
    assert text.count("## Last Section") == 1
    assert "fresh" in text


def test_set_json_mode_reports_section_and_stats(cli, ws_dir):
    _seed_doc(cli)
    r = cli("--json", "set", "docs/rag.md#Chunking/Overlap", stdin="x\n")
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["section"] == "docs/rag.md#Chunking/Overlap"
    assert data["ok"] is True
    assert "headings" in data


def test_set_snapshots_before_replacing(cli, ws_dir):
    _seed_doc(cli)
    cli("set", "docs/rag.md#Chunking/Overlap", stdin="replaced once\n")
    r = cli("log", "docs/rag.md")
    assert r.returncode == 0
    assert "set" in r.stdout
