"""Tests for the write surface: new, write, append, tag (plan.md §3)."""

from __future__ import annotations

import json


def test_new_creates_file_with_frontmatter(cli, ws_dir):
    r = cli("new", "notes/rag.md", "--about", "retrieval strategies", "--tags", "research,wip",
            stdin="Some body.\n")
    assert r.returncode == 0
    assert "stowed notes/rag.md" in r.stdout

    text = (ws_dir / "notes/rag.md").read_text()
    assert text.startswith("---\n")
    assert "about: retrieval strategies" in text
    assert "tags: [research, wip]" in text
    assert "Some body." in text


def test_new_refuses_existing(cli):
    cli("new", "a.md", "--about", "x", "--tags", "x")
    r = cli("new", "a.md", "--about", "y", "--tags", "y")
    assert r.returncode != 0
    assert "E_EXISTS" in r.stderr


def test_new_without_stdin_body_still_creates_frontmatter(cli, ws_dir):
    r = cli("new", "b.md", "--about", "x", "--tags", "")
    assert r.returncode == 0
    text = (ws_dir / "b.md").read_text()
    assert text.startswith("---\n")
    assert "tags: []" in text


def test_new_default_title_from_filename(cli, ws_dir):
    cli("new", "notes/my-topic.md", "--about", "x", "--tags", "")
    text = (ws_dir / "notes/my-topic.md").read_text()
    assert "title: my-topic" in text


def test_new_explicit_title_wins(cli, ws_dir):
    cli("new", "notes/x.md", "--about", "x", "--tags", "", "--title", "Custom Title")
    text = (ws_dir / "notes/x.md").read_text()
    assert "title: Custom Title" in text


def test_write_creates_new_file(cli, ws_dir):
    r = cli("write", "notes/x.md", stdin="hello world\n")
    assert r.returncode == 0
    assert (ws_dir / "notes/x.md").exists()
    assert "hello world" in (ws_dir / "notes/x.md").read_text()


def test_write_overwrite_preserves_frontmatter(cli, ws_dir):
    cli("new", "notes/x.md", "--about", "topic", "--tags", "a,b", stdin="orig body\n")
    r = cli("write", "notes/x.md", stdin="## New\n\nnew body\n")
    assert r.returncode == 0
    text = (ws_dir / "notes/x.md").read_text()
    assert "about: topic" in text
    assert "tags: [a, b]" in text
    assert "new body" in text
    assert "orig body" not in text


def test_write_content_with_own_frontmatter_overrides(cli, ws_dir):
    cli("new", "notes/x.md", "--about", "topic", "--tags", "a,b", stdin="orig\n")
    r = cli("write", "notes/x.md", stdin='---\nabout: "different"\n---\n\nreplacement\n')
    assert r.returncode == 0
    text = (ws_dir / "notes/x.md").read_text()
    assert "about: different" in text
    assert "topic" not in text
    # frontmatter keys not present in the new content still carry forward
    assert "tags: [a, b]" in text


def test_write_non_md_file_no_frontmatter_injected(cli, ws_dir):
    r = cli("write", "notes.txt", stdin="plain text\n")
    assert r.returncode == 0
    text = (ws_dir / "notes.txt").read_text()
    assert text == "plain text\n"


def test_append_keeps_exactly_one_blank_line(cli, ws_dir):
    cli("write", "notes/x.md", stdin="first paragraph\n")
    r = cli("append", "notes/x.md", stdin="second paragraph\n")
    assert r.returncode == 0
    text = (ws_dir / "notes/x.md").read_text()
    assert "first paragraph\n\nsecond paragraph" in text
    assert "\n\n\n" not in text


def test_append_creates_missing_file(cli, ws_dir):
    r = cli("append", "notes/new.md", stdin="content\n")
    assert r.returncode == 0
    assert (ws_dir / "notes/new.md").exists()
    assert "content" in (ws_dir / "notes/new.md").read_text()


def test_append_multiple_times_stays_single_blank_line(cli, ws_dir):
    cli("write", "a.md", stdin="one\n")
    cli("append", "a.md", stdin="two\n")
    cli("append", "a.md", stdin="three\n")
    text = (ws_dir / "a.md").read_text()
    assert "one\n\ntwo\n\nthree" in text


def test_write_dup_heading_warns_not_fails(cli):
    r = cli("write", "d.md", stdin="## Same\nfoo\n## Same\nbar\n")
    assert r.returncode == 0
    assert "W_DUP_HEADING" in r.stderr
    assert "Same" in r.stderr


def test_write_reports_unresolved_links(cli):
    r = cli("write", "d.md", stdin="see [[nowhere]] please\n")
    assert r.returncode == 0
    assert "1 unresolved" in r.stdout


def test_tag_add_and_remove_and_bare_word(cli, ws_dir):
    cli("new", "notes/x.md", "--about", "t", "--tags", "research,wip")
    r = cli("tag", "notes/x.md", "+stale", "-wip", "extra")
    assert r.returncode == 0
    text = (ws_dir / "notes/x.md").read_text()
    assert "stale" in text
    assert "extra" in text
    assert "wip" not in text
    assert "research" in text


def test_tag_no_changes_errors(cli):
    cli("new", "notes/x.md", "--about", "t", "--tags", "research")
    r = cli("tag", "notes/x.md")
    assert r.returncode != 0


def test_tag_missing_file_errors(cli):
    r = cli("tag", "nope.md", "+x")
    assert r.returncode != 0
    assert "E_NOT_FOUND" in r.stderr


def test_json_mode_write(cli):
    r = cli("--json", "write", "j.md", stdin="hi\n")
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["path"] == "j.md"
    assert data["ok"] is True
    assert "headings" in data and "links" in data and "chunks" in data


def test_json_mode_new(cli):
    r = cli("--json", "new", "j.md", "--about", "x", "--tags", "a,b", stdin="body\n")
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["path"] == "j.md"
    assert data["ok"] is True
