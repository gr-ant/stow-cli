"""Create a workspace here.

Also writes the command reference into AGENTS.md. A CLI the agent doesn't know
exists gets used zero times, and in a design where the write path *is* the
product, an unused CLI is a workspace with no index at all (plan.md §13).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .. import out
from ..config import TEMPLATE
from ..db import connect
from ..errors import stwError
from ..index import reindex
from ..workspace import Workspace

MARK_START = "<!-- stw:start -->"
MARK_END = "<!-- stw:end -->"

REFERENCE = """\
## Workspace (stw)

This directory is a stw workspace. Write through `stw` rather than editing
files directly: the index, the link graph, and `map.md` stay correct for free,
and `stw undo` can recover an overwrite.

    stw map                                   # read this first — what's here and why
    stw new notes/rag.md --about "retrieval strategies" --tags research
    stw write notes/rag.md   < content        # create or overwrite
    stw append notes/rag.md  < content
    stw set notes/rag.md#Chunking/Overlap < content   # replace ONE section
    stw read notes/rag.md [--section S] [--lines A:B]
    stw outline notes/rag.md                  # heading tree, cheaper than reading
    stw find "chunk overlap tradeoffs" -k 5   # hybrid semantic + keyword search
    stw grep PATTERN                          # exact match
    stw backlinks notes/rag.md                # what else references this
    stw mv A B    stw rm A    stw tag A +done -wip
    stw log notes/rag.md   stw undo notes/rag.md
    stw sql data/experiments.db "SELECT ..."  # DuckDB artifacts
    stw doctor                                # broken links, drift, orphans

`stw set PATH#Heading/Sub` edits one section without rewriting the file — use it
instead of re-writing a whole document to change a paragraph. Add `--json` to any
command when piping. Run `stw help` for the full surface.
"""


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", nargs="?", default=".", help="directory to initialize")
    parser.add_argument("--force", action="store_true", help="reinitialize an existing workspace")
    parser.add_argument("--no-agents-md", action="store_true", help="skip the AGENTS.md reference")


def run(ws, conn, args) -> int:
    root = Path(getattr(args, "dir", None) or args.path).resolve()
    root.mkdir(parents=True, exist_ok=True)
    stw_dir = root / ".stw"

    if stw_dir.exists() and not args.force:
        raise stwError(
            "E_EXISTS",
            f"{root} is already a workspace",
            "Use --force to reinitialize, or `stw sync` to repair the index.",
        )

    stw_dir.mkdir(exist_ok=True)
    (stw_dir / "objects").mkdir(exist_ok=True)
    cfg = stw_dir / "config.toml"
    if not cfg.exists():
        cfg.write_text(TEMPLATE)

    wrote_ref = False
    if not args.no_agents_md:
        for name in ("AGENTS.md", "CLAUDE.md"):
            p = root / name
            if name == "CLAUDE.md" and not p.exists():
                continue
            wrote_ref |= _write_reference(p)

    # Index last, so the reference file we just wrote is itself in the registry
    # and the first `stw sync` has nothing to catch up on.
    workspace = Workspace.at(root)
    conn = connect(workspace.index_path, create=True)
    try:
        indexed = 0
        for rel in workspace.walk():
            reindex(workspace, conn, rel)
            indexed += 1
    finally:
        conn.close()

    out.emit(
        f"initialized {root} · {indexed} files indexed"
        + (" · AGENTS.md updated" if wrote_ref else ""),
        {"root": str(root), "indexed": indexed, "reference": wrote_ref},
    )
    return 0


def _write_reference(path: Path) -> bool:
    block = f"{MARK_START}\n{REFERENCE}{MARK_END}\n"
    if path.exists():
        text = path.read_text()
        if MARK_START in text and MARK_END in text:
            head = text[: text.index(MARK_START)]
            tail = text[text.index(MARK_END) + len(MARK_END) :].lstrip("\n")
            path.write_text(head + block + tail)
        else:
            sep = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
            path.write_text(text + sep + block)
    else:
        path.write_text(block)
    return True
