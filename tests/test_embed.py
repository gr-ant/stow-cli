"""Embedder sidecar (plan.md §6)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from tests.helpers import seed

ROOT = Path(__file__).resolve().parent.parent
EMBED_HASH = ROOT / "examples" / "embed_hash.py"


def _configure(ws_dir: Path, *, cmd=None, dim=16, script_dim=None, batch=4, max_inline=256) -> None:
    """Point .stow/config.toml at the deterministic embed_hash.py sidecar
    (or a caller-supplied `cmd`), for tests that need an actual embedder."""
    if cmd is None:
        cmd = [sys.executable, str(EMBED_HASH), "--dim", str(script_dim if script_dim is not None else dim)]
    cmd_toml = ", ".join(json.dumps(c) for c in cmd)
    text = f"""\
[workspace]
include = ["**/*.md", "**/*.db"]
exclude = ["node_modules/**", ".stow/**", ".git/**", "map.md", "AGENTS.md", "CLAUDE.md"]

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
    (ws_dir / ".stow" / "config.toml").write_text(text)


def _section(n: int, words: str = "content") -> str:
    return f"# Doc\n\n## Section\n\n" + f"{words} {n} " * 20


# --------------------------------------------------------------------------
# vector <-> blob roundtrip (no CLI needed)
# --------------------------------------------------------------------------
def test_vector_blob_roundtrip():
    from stow.embedder import blob_to_vector, vector_to_blob

    vec = [0.5, -1.25, 3.0, 0.0, -0.125]
    blob = vector_to_blob(vec)
    assert isinstance(blob, bytes)
    assert len(blob) == 4 * len(vec)
    assert blob_to_vector(blob) == vec


# --------------------------------------------------------------------------
# stw embed
# --------------------------------------------------------------------------
def test_embed_clears_dirty_chunks(ws_dir, cli):
    _configure(ws_dir)
    seed(ws_dir, {"notes/a.md": _section(1)})

    r = cli("embed", "--json")
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["embedded"] > 0
    assert data["remaining"] == 0
    assert data["batches"] >= 1


def test_embed_is_idempotent_when_nothing_dirty(ws_dir, cli):
    _configure(ws_dir)
    seed(ws_dir, {"notes/a.md": _section(1)})
    cli("embed")
    r = cli("embed", "--json")
    data = json.loads(r.stdout)
    assert data["embedded"] == 0
    assert data["batches"] == 0
    assert data["remaining"] == 0


def test_embed_all_reembeds_everything(ws_dir, cli):
    _configure(ws_dir)
    seed(ws_dir, {"notes/a.md": _section(1)})
    cli("embed")
    r = cli("embed", "--all", "--json")
    data = json.loads(r.stdout)
    assert data["embedded"] > 0
    assert data["remaining"] == 0


def test_embed_batches_independently_via_small_batch_size(ws_dir, cli):
    _configure(ws_dir, batch=1)
    seed(ws_dir, {f"notes/n{i}.md": _section(i) for i in range(3)})
    r = cli("embed", "--json")
    data = json.loads(r.stdout)
    assert data["batches"] == data["embedded"]  # batch size 1 -> one batch per chunk (>= 3 chunks)
    assert data["batches"] >= 3


def test_no_embedder_configured_raises_e_no_embedder(ws_dir, cli):
    # Default config.toml has embed.cmd commented out (empty list).
    seed(ws_dir, {"notes/a.md": _section(1)})
    r = cli("embed")
    assert r.returncode != 0
    assert "E_NO_EMBEDDER" in r.stderr


def test_dim_mismatch_raises_e_dim_mismatch(ws_dir, cli):
    # config declares dim=8 but the sidecar is told to emit 16-d vectors.
    _configure(ws_dir, dim=8, script_dim=16)
    seed(ws_dir, {"notes/a.md": _section(1)})
    r = cli("embed")
    assert r.returncode != 0
    assert "E_DIM_MISMATCH" in r.stderr


def test_embed_failure_leaves_dirty_flags_for_retry(ws_dir, cli):
    broken = ws_dir / "broken_embed.py"
    broken.write_text("import sys\nsys.stderr.write('boom')\nsys.exit(1)\n")
    _configure(ws_dir, cmd=[sys.executable, str(broken)])
    seed(ws_dir, {"notes/a.md": _section(1)})

    r = cli("embed")
    assert r.returncode != 0
    assert "E_EMBED" in r.stderr
    assert "boom" in r.stderr
    assert "dirty" in r.stderr

    # Retry with a working sidecar resumes from where it left off — nothing
    # was lost even though the first attempt failed mid-batch.
    _configure(ws_dir)
    r2 = cli("embed", "--json")
    assert r2.returncode == 0
    data = json.loads(r2.stdout)
    assert data["remaining"] == 0
    assert data["embedded"] > 0


def test_embed_malformed_output_line_fails_cleanly(ws_dir, cli):
    bad = ws_dir / "bad_output_embed.py"
    bad.write_text("print('not json')\n")
    _configure(ws_dir, cmd=[sys.executable, str(bad)])
    seed(ws_dir, {"notes/a.md": _section(1)})
    r = cli("embed")
    assert r.returncode != 0
    assert "E_EMBED" in r.stderr
