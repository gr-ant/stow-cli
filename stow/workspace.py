"""Workspace root discovery and path normalization.

Every path that crosses into the index is a workspace-relative POSIX string:
'research/rag.md'. Never absolute, never './'-prefixed, never backslashed.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path

from .errors import NoWorkspace, StowError
from . import config as config_mod

STOW_DIR = ".stow"
INDEX_NAME = "stow.db"


@dataclass
class Workspace:
    root: Path
    config: dict

    # -- discovery ---------------------------------------------------------
    @classmethod
    def find(cls, start: Path | None = None) -> "Workspace":
        """Walk up from `start` looking for .stow/. Raises NoWorkspace."""
        cur = (start or Path.cwd()).resolve()
        for cand in [cur, *cur.parents]:
            if (cand / STOW_DIR).is_dir():
                return cls(root=cand, config=config_mod.load(cand))
        raise NoWorkspace(str(cur))

    @classmethod
    def at(cls, root: Path) -> "Workspace":
        root = root.resolve()
        return cls(root=root, config=config_mod.load(root))

    # -- paths -------------------------------------------------------------
    @property
    def stow_dir(self) -> Path:
        return self.root / STOW_DIR

    @property
    def index_path(self) -> Path:
        return self.stow_dir / INDEX_NAME

    @property
    def objects_dir(self) -> Path:
        return self.stow_dir / "objects"

    def rel(self, path: str | Path) -> str:
        """Normalize any user-supplied path to a workspace-relative POSIX string."""
        p = Path(path)
        if not p.is_absolute():
            p = (Path.cwd() / p) if Path.cwd() != self.root else (self.root / p)
        try:
            r = p.resolve().relative_to(self.root)
        except ValueError:
            raise StowError(
                "E_OUTSIDE",
                f"{path} is outside the workspace ({self.root})",
                "Paths must be inside the workspace root.",
            ) from None
        s = r.as_posix()
        if s in (".", ""):
            raise StowError("E_USAGE", "expected a file path, got the workspace root")
        return s

    def abs(self, rel: str) -> Path:
        return self.root / rel

    # -- include/exclude ---------------------------------------------------
    def is_included(self, rel: str) -> bool:
        inc = self.config["workspace"]["include"]
        exc = self.config["workspace"]["exclude"]
        if any(_match(rel, pat) for pat in exc):
            return False
        return any(_match(rel, pat) for pat in inc)

    def walk(self) -> list[str]:
        """Every included path in the workspace, sorted."""
        out: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in (STOW_DIR, ".git", "node_modules")]
            for fn in filenames:
                rel = (Path(dirpath) / fn).relative_to(self.root).as_posix()
                if self.is_included(rel):
                    out.append(rel)
        return sorted(out)


def _match(rel: str, pattern: str) -> bool:
    """Glob match with '**' semantics good enough for include/exclude lists."""
    if pattern.startswith("**/"):
        tail = pattern[3:]
        return fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rel, tail) or fnmatch.fnmatch(
            rel, f"*/{tail}"
        ) or any(fnmatch.fnmatch(part, tail) for part in [rel.rsplit("/", 1)[-1]])
    if pattern.endswith("/**"):
        head = pattern[:-3]
        return rel == head or rel.startswith(head + "/")
    return fnmatch.fnmatch(rel, pattern)


def kind_of(rel: str) -> str:
    """'md' | 'db' | 'other' — the registry's `kind` column."""
    low = rel.lower()
    if low.endswith(".md") or low.endswith(".markdown"):
        return "md"
    if low.endswith(".db") or low.endswith(".duckdb"):
        return "db"
    return "other"
