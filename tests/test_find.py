"""stw find — hybrid semantic + BM25 search (plan.md §8)."""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

from tests.helpers import seed

from stw.vectors import _cosine_topk_py, cosine_topk

ROOT = Path(__file__).resolve().parent.parent
EMBED_HASH = ROOT / "examples" / "embed_hash.py"


def _configure(ws_dir: Path, *, dim=16, batch=8, max_inline=256) -> None:
    cmd = [sys.executable, str(EMBED_HASH), "--dim", str(dim)]
    cmd_toml = ", ".join(json.dumps(c) for c in cmd)
    text = f"""\
[workspace]
include = ["**/*.md", "**/*.db"]
exclude = ["node_modules/**", ".stw/**", ".git/**", "map.md", "AGENTS.md", "CLAUDE.md"]

[embed]
cmd = [{cmd_toml}]
model = "test-hash"
dim = {dim}
batch = {batch}
prefix_doc = "passage: "
prefix_query = "query: "
mode = "deferred"
max_inline = {max_inline}

[chunk]
max_chars = 400
min_chars = 40
overlap = 0.15
"""
    (ws_dir / ".stw" / "config.toml").write_text(text)


def _vec_blob(xs: list[float]) -> bytes:
    return struct.pack(f"<{len(xs)}f", *xs)


# --------------------------------------------------------------------------
# CLI-level find behavior
# --------------------------------------------------------------------------
def test_hybrid_find_returns_scored_hits_with_line_range(ws_dir, cli):
    _configure(ws_dir)
    seed(
        ws_dir,
        {
            "notes/rag.md": "# Rag\n\n## Chunking\n\n" + ("Overlap keeps context across boundaries. " * 8),
            "notes/other.md": "# Other\n\n## Cooking\n\n" + ("A recipe for pasta with garlic. " * 8),
        },
    )
    cli("embed")
    r = cli("find", "overlap boundaries context", "-k", "3")
    assert r.returncode == 0
    assert "rag.md" in r.stdout
    assert "#Chunking" in r.stdout
    assert " L" in r.stdout  # e.g. "L5-9"


def test_text_only_finds_exact_keyword(ws_dir, cli):
    _configure(ws_dir)
    seed(ws_dir, {"notes/a.md": "# A\n\n## One\n\n" + ("unique token zzyzx appears here. " * 6)})
    cli("embed")
    r = cli("find", "zzyzx", "--text-only")
    assert r.returncode == 0
    assert "notes/a.md" in r.stdout


def test_vector_only_flag(ws_dir, cli):
    _configure(ws_dir)
    seed(ws_dir, {"notes/a.md": "# A\n\n## One\n\n" + ("some content about apples and orchards. " * 6)})
    cli("embed")
    r = cli("find", "apples orchards", "--vector-only")
    assert r.returncode == 0
    assert "notes/a.md" in r.stdout


def test_vector_only_and_text_only_are_mutually_exclusive(ws_dir, cli):
    r = cli("find", "anything", "--vector-only", "--text-only")
    assert r.returncode != 0


def test_no_embedder_configured_falls_back_to_text_only(ws_dir, cli):
    # default config.toml: embed.cmd is empty
    seed(ws_dir, {"notes/a.md": "# A\n\n## One\n\n" + ("distinct token wombat sits here. " * 6)})
    r = cli("find", "wombat")
    assert r.returncode == 0
    assert "notes/a.md" in r.stdout
    assert "W_NO_EMBEDDER" in r.stderr


def test_fts_sanitizes_special_syntax_without_crashing(ws_dir, cli):
    _configure(ws_dir)
    seed(ws_dir, {"notes/a.md": "# A\n\n## One\n\n" + ("plain text body. " * 6)})
    cli("embed")
    r = cli("find", 'weird "query" (with) syntax* OR NEAR', "--text-only")
    assert r.returncode == 0  # must not raise on FTS5 operator syntax


def test_dirty_backfill_respects_max_inline_and_warns(ws_dir, cli):
    _configure(ws_dir, max_inline=1, batch=1)
    files = {
        f"notes/n{i}.md": f"# N{i}\n\n## Section\n\n" + (f"shared keyword content number {i}. " * 8)
        for i in range(4)
    }
    seed(ws_dir, files)
    r = cli("find", "shared keyword content")
    assert r.returncode == 0
    assert "W_DIRTY" in r.stderr


def test_under_filter_restricts_to_path_prefix(ws_dir, cli):
    _configure(ws_dir)
    seed(
        ws_dir,
        {
            "notes/keep/a.md": "# A\n\n## One\n\n" + ("shared banana keyword here. " * 6),
            "other/b.md": "# B\n\n## One\n\n" + ("shared banana keyword here. " * 6),
        },
    )
    cli("embed")
    r = cli("find", "banana", "--under", "notes", "--text-only")
    assert r.returncode == 0
    assert "notes/keep/a.md" in r.stdout
    assert "other/b.md" not in r.stdout


def test_find_json_shape(ws_dir, cli):
    _configure(ws_dir)
    seed(ws_dir, {"notes/a.md": "# A\n\n## One\n\n" + ("json shape test content here. " * 6)})
    cli("embed")
    r = cli("find", "json shape test", "--json")
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert "hits" in data and isinstance(data["hits"], list)
    assert data["hits"], "expected at least one hit"
    hit = data["hits"][0]
    assert {"score", "path", "heading_path", "line_start", "line_end", "text"} <= hit.keys()


def test_full_flag_prints_whole_chunk(ws_dir, cli):
    _configure(ws_dir)
    body = "line alpha unique.\nline beta unique.\nline gamma unique.\nline delta unique."
    seed(ws_dir, {"notes/a.md": f"# A\n\n## One\n\n{body}"})
    cli("embed")
    r = cli("find", "unique", "--full", "--text-only")
    assert r.returncode == 0
    assert "line alpha unique." in r.stdout
    assert "line delta unique." in r.stdout


# --------------------------------------------------------------------------
# vector scoring: numpy path and the pure-Python fallback, called directly
# --------------------------------------------------------------------------
def test_pure_python_scorer_ranks_by_cosine_similarity():
    candidates = [
        ("a", _vec_blob([1.0, 0.0, 0.0])),
        ("b", _vec_blob([0.0, 1.0, 0.0])),
        ("c", _vec_blob([0.9, 0.1, 0.0])),
    ]
    top = _cosine_topk_py([1.0, 0.0, 0.0], candidates, 3)
    assert top[0][0] == "a"
    assert abs(top[0][1] - 1.0) < 1e-6
    assert [k for k, _ in top] == ["a", "c", "b"]


def test_numpy_and_pure_python_scorers_agree():
    candidates = [
        ("a", _vec_blob([1.0, 0.0, 0.0])),
        ("b", _vec_blob([0.0, 1.0, 0.0])),
        ("c", _vec_blob([0.7, 0.7, 0.0])),
    ]
    query = [1.0, 0.0, 0.0]
    np_result = cosine_topk(query, candidates, 3)
    py_result = _cosine_topk_py(query, candidates, 3)
    assert [k for k, _ in np_result] == [k for k, _ in py_result]
    for (ka, sa), (kb, sb) in zip(np_result, py_result):
        assert ka == kb
        assert abs(sa - sb) < 1e-5


def test_cosine_topk_empty_candidates():
    assert cosine_topk([1.0, 0.0], [], 5) == []
    assert _cosine_topk_py([1.0, 0.0], [], 5) == []
