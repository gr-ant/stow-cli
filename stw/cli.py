"""CLI dispatch.

Commands are lazily imported: `stw read` never imports duckdb, numpy, or the
write path. That is the entire answer to the startup-latency complaint in
plan.md §4 — confine the heavy import to the two commands that need it.

Every command module exposes:
    add_arguments(parser) -> None
    run(ws, conn, args)   -> int      # ws/conn are None for commands in NO_WS
"""

from __future__ import annotations

import argparse
import importlib
import sqlite3
import sys
from pathlib import Path

from . import __version__
from .db import connect
from .errors import stwError, Usage
from . import out
from .workspace import Workspace

# name -> (module suffix, one-line summary for `stw help`)
COMMANDS: dict[str, tuple[str, str]] = {
    # write surface (plan.md §3)
    "init":      ("init",      "create a workspace here"),
    "new":       ("new",       "create a file with frontmatter"),
    "write":     ("write",     "create or overwrite from stdin"),
    "append":    ("append",    "append stdin to a file"),
    "set":       ("set",       "replace one section (PATH#Heading/Sub)"),
    "mv":        ("mv",        "move a file, rewriting inbound links"),
    "rm":        ("rm",        "remove a file, refusing if backlinked"),
    "tag":       ("tag",       "add/remove tags (+tag -tag)"),
    # history (plan.md §3)
    "log":       ("log",       "version history for a path"),
    "undo":      ("undo",      "restore the previous version"),
    "restore":   ("restore",   "restore a specific version (PATH@sha)"),
    "gc":        ("gc",        "prune old versions and orphaned embeddings"),
    # read surface (plan.md §8)
    "read":      ("read",      "print a file, section, or line range"),
    "outline":   ("outline",   "heading tree of a file"),
    "ls":        ("ls",        "list files by glob or tag"),
    "grep":      ("grep",      "literal/regex search across the workspace"),
    "map":       ("map",       "render the workspace orientation file"),
    "links":     ("links",     "outbound links from a file"),
    "backlinks": ("backlinks", "what links to a file"),
    "doctor":    ("doctor",    "report broken links, orphans, drift"),
    "sync":      ("sync",      "repair the index from files on disk"),
    # retrieval (plan.md §6, §8)
    "embed":     ("embed",     "embed dirty chunks via the sidecar"),
    "find":      ("find",      "hybrid semantic + BM25 search"),
    # artifacts (plan.md §4)
    "db":        ("db",        "create/import/export DuckDB artifacts"),
    "sql":       ("sql",       "run SQL against a DuckDB artifact"),
}

NO_WS = {"init"}
READ_ONLY = {"read", "outline", "ls", "grep", "links", "backlinks", "log", "map", "doctor"}


def _split_globals(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    """Pull global flags out of anywhere in argv so they work pre- or post-command."""
    json_mode = False
    directory: str | None = None
    rest: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--json":
            json_mode = True
        elif a in ("-C", "--dir"):
            i += 1
            if i >= len(argv):
                raise Usage("-C needs a directory")
            directory = argv[i]
        elif a.startswith("--dir="):
            directory = a.split("=", 1)[1]
        else:
            rest.append(a)
        i += 1
    return argparse.Namespace(json=json_mode, dir=directory), rest


def usage() -> str:
    width = max(len(k) for k in COMMANDS)
    groups = [
        ("write", ["new", "write", "append", "set", "mv", "rm", "tag"]),
        ("history", ["log", "undo", "restore", "gc"]),
        ("read", ["read", "outline", "ls", "grep", "map", "links", "backlinks"]),
        ("search", ["find", "embed"]),
        ("data", ["db", "sql"]),
        ("upkeep", ["init", "sync", "doctor"]),
    ]
    lines = [f"stw {__version__} — workspace manager for agents", ""]
    for title, names in groups:
        lines.append(f"{title}:")
        for n in names:
            lines.append(f"  {n:<{width}}  {COMMANDS[n][1]}")
        lines.append("")
    lines.append("global: --json   -C DIR")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        gl, rest = _split_globals(argv)
    except stwError as e:
        out.error(e.render())
        return e.exit_code

    out.set_json(gl.json)

    if not rest or rest[0] in ("-h", "--help", "help"):
        print(usage())
        return 0
    if rest[0] in ("-V", "--version"):
        print(__version__)
        return 0

    name, args_rest = rest[0], rest[1:]
    if name not in COMMANDS:
        near = [c for c in COMMANDS if c.startswith(name[:2])]
        out.error(
            f"E_USAGE: unknown command '{name}'."
            + (f" Did you mean: {', '.join(near[:4])}?" if near else " Run `stw help`.")
        )
        return 2

    mod_name, summary = COMMANDS[name]
    try:
        mod = importlib.import_module(f".commands.{mod_name}", package="stw")
    except ModuleNotFoundError as e:
        out.error(f"E_UNIMPLEMENTED: `stw {name}` is not built yet ({e}).")
        return 3

    parser = argparse.ArgumentParser(prog=f"stw {name}", description=summary)
    mod.add_arguments(parser)
    args = parser.parse_args(args_rest)
    args._command = name
    args.json = gl.json

    ws = None
    conn: sqlite3.Connection | None = None
    try:
        if name not in NO_WS:
            start = Path(gl.dir) if gl.dir else Path.cwd()
            ws = Workspace.find(start)
            conn = connect(ws.index_path)
        elif gl.dir:
            args.dir = gl.dir
        return int(mod.run(ws, conn, args) or 0)
    except stwError as e:
        if out.is_json():
            import json as _json
            print(_json.dumps(e.to_json()))
        else:
            out.error(e.render())
        return e.exit_code
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        out.error("interrupted")
        return 130
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
