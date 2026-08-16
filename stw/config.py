"""Config loading. Defaults live here; .stw/config.toml overrides them."""

from __future__ import annotations

import copy
import tomllib
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "workspace": {
        "include": ["**/*.md", "**/*.db"],
        # Generated files stay out of the index: map.md listing itself, or `find`
        # returning stw's own usage blurb, is noise the agent pays for.
        "exclude": ["node_modules/**", ".stw/**", ".git/**", "map.md", "AGENTS.md", "CLAUDE.md"],
    },
    "map": {"regenerate": "on-write", "depth": 1},
    "history": {"enabled": True, "keep": 50},
    "embed": {
        "cmd": [],
        "model": "bge-small-en-v1.5",
        "dim": 384,
        "batch": 64,
        "prefix_doc": "passage: ",
        "prefix_query": "query: ",
        "mode": "deferred",
        "max_inline": 256,
    },
    "chunk": {"max_chars": 1200, "min_chars": 200, "overlap": 0.15},
}

TEMPLATE = """\
[workspace]
include = ["**/*.md", "**/*.db"]
exclude = ["node_modules/**", ".stw/**", ".git/**", "map.md", "AGENTS.md", "CLAUDE.md"]

[map]
regenerate = "on-write"      # on-write | on-demand
depth      = 1

[history]
enabled = true
keep    = 50                 # versions kept per path

[embed]
# cmd        = ["python", ".stw/embed.py"]
model        = "bge-small-en-v1.5"
dim          = 384
batch        = 64
prefix_doc   = "passage: "
prefix_query = "query: "
mode         = "deferred"    # deferred | eager | off
max_inline   = 256

[chunk]
max_chars = 1200
min_chars = 200
overlap   = 0.15
"""


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(root: Path) -> dict[str, Any]:
    """Load config for a workspace root, merged over DEFAULTS."""
    p = root / ".stw" / "config.toml"
    if not p.exists():
        return copy.deepcopy(DEFAULTS)
    with p.open("rb") as fh:
        return _merge(DEFAULTS, tomllib.load(fh))
