# Implementation contract

Read `plan.md` first — it is the spec. This file is the API the foundation
already provides so three parallel agents don't collide.

**Do not edit** any file under `stow/` that is not in your assigned list, and do
not edit `stow/cli.py` (every command is already registered) or `plan.md`.

## Environment

- `./stw <args>` runs the CLI (uses `.venv`). Also `.venv/bin/python -m stow`.
- Tests: `.venv/bin/python -m pytest tests -q` (numpy, duckdb, pytest installed).
- Python 3.12. stdlib-first. `numpy` and `duckdb` must be **lazily imported**
  inside the functions that need them, never at module top level.

## Command module shape

Every file in `stow/commands/` exposes exactly:

```python
def add_arguments(parser: argparse.ArgumentParser) -> None: ...
def run(ws, conn, args) -> int: ...   # 0 on success; raise StowError otherwise
```

`ws` is a `Workspace`, `conn` an open sqlite3 connection (WAL, row_factory=Row,
`isolation_level=None` — wrap writes in `with db.tx(conn):`). Both are `None`
only for `init`. `args.json` is set. Return 0; signal failure by raising a
`StowError` subclass — `cli.main` renders it to stderr and picks the exit code.

## Foundation API

`stow/workspace.py`
- `Workspace.find(start) -> Workspace` (raises `NoWorkspace`), `Workspace.at(root)`
- `.root`, `.stow_dir`, `.index_path`, `.objects_dir`, `.config` (merged dict)
- `.rel(path) -> str` — normalize anything to workspace-relative POSIX. Use it on
  every user-supplied path. `.abs(rel) -> Path`
- `.is_included(rel) -> bool`, `.walk() -> list[str]`, `kind_of(rel) -> 'md'|'db'|'other'`

`stow/db.py` — `connect(path, create=False)`, `ensure_schema(conn)`, `tx(conn)`
context manager, `fts_reindex(conn)`, `SCHEMA` (read it — it is the source of
truth for column names).

`stow/md.py`
- `split_frontmatter(text) -> (fm, body, line_offset)`, `parse_frontmatter`,
  `render_frontmatter(fm) -> str`, `ensure_frontmatter(text, defaults) -> str`
- `parse_headings(text) -> list[Heading]` with `.text .level .heading_path
  .slug_path .line_start .line_end .byte_start .byte_end .body_byte_start
  .content_sha .ordinal`. H1 is the title and never an ancestor of a path.
  Fenced code blocks are skipped.
- `section_body(text, h) -> str`, `resolve_section(path, headings, addr) -> Heading`
  (raises `AmbiguousHeading` / `NoSuchHeading`), `replace_section(text, h, body) -> str`
- `split_address('a.md#H/S') -> ('a.md', 'H/S')`, `slugify`, `slug_path`
- `parse_links(text) -> list[Link]` with `.raw .target .anchor .kind .line .alias`
- `title_of(text, fm, fallback)`

`stow/index.py`
- `reindex(ws, conn, rel) -> stats` — the only correct way to index a file.
  stats = `{path, kind, size, headings, links, unresolved, chunks, dup_headings}`
- `remove(conn, rel)`, `resolve_link(ws, conn, src, target)`,
  `reresolve_incoming(ws, conn) -> int`, `backlinks(conn, rel) -> rows`,
  `stale_check(ws, conn, rel) -> bool`, `now_iso()`
- `reindex_chunks(...)` preserves `dirty=0` when `embed_sha` is already in `embeddings`.

`stow/out.py` — `emit(human, data)`, `line`, `raw`, `warn(code, msg, hint)`,
`error`, `is_json()`, `fmt_size`, `fmt_count`, `excerpt`. **Every command must
support `--json`**: build a dict and call `out.emit(human_string, data_dict)`.

`stow/errors.py` — `StowError(code, message, hint)` plus the named subclasses in
plan.md §11. Add new subclasses there only if you own that file.

`stow/chunker.py` — `Chunk`, `chunk_document(path, text, cfg_chunk, headings)`,
`embed_input(heading_path, text, prefix_doc)`. **The embedded string carries the
heading path only, never the file path** (plan.md §5). Owned by agent C.

`stow/hashing.py` — `sha256_bytes`, `sha256_text`, `short`.

## Rules that are not negotiable

1. Terse output. Confirmation lines look like
   `stowed research/rag.md · 1.8k · 6 headings · 4 links (1 unresolved)`.
2. Errors name the next action. Warnings are `W_*` on stderr and never stop a
   command from answering.
3. No lock file, no flock. WAL + `busy_timeout` is the concurrency story.
4. Read commands call `index.stale_check` on paths they touch and
   `out.warn("W_STALE", ...)` when the file moved underneath — then answer anyway.
5. Tests go in `tests/` with the filenames listed in your brief. Use the `cli`
   fixture from `conftest.py` (subprocess, real workspace in a tmp dir).
