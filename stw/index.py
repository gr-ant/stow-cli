"""Indexing: file on disk -> rows in the index.

Every write command funnels through reindex(). Because writes are mediated the
index is correct by construction; `stw sync` (plan.md §12) is the repair path
for the times something edits a file behind stw's back.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import md
from .chunker import chunk_document
from .hashing import sha256_bytes
from .workspace import Workspace, kind_of


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def reindex(ws: Workspace, conn: sqlite3.Connection, rel: str) -> dict:
    """(Re)index one path from its bytes on disk. Returns a stats dict.

    stats: {path, kind, size, headings, links, unresolved, chunks, dup_headings}
    """
    abspath = ws.abs(rel)
    if not abspath.exists():
        remove(conn, rel)
        return {"path": rel, "kind": kind_of(rel), "size": 0, "headings": 0,
                "links": 0, "unresolved": 0, "chunks": 0, "dup_headings": []}

    st = abspath.stat()
    kind = kind_of(rel)
    data = abspath.read_bytes()
    sha = sha256_bytes(data)

    if kind != "md":
        conn.execute(
            """INSERT INTO files(path, kind, size, mtime_ns, sha256, title, about,
                                 tags, frontmatter, indexed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(path) DO UPDATE SET
                 kind=excluded.kind, size=excluded.size, mtime_ns=excluded.mtime_ns,
                 sha256=excluded.sha256, indexed_at=excluded.indexed_at""",
            (rel, kind, st.st_size, st.st_mtime_ns, sha, Path(rel).name, None,
             "[]", "{}", now_iso()),
        )
        return {"path": rel, "kind": kind, "size": st.st_size, "headings": 0,
                "links": 0, "unresolved": 0, "chunks": 0, "dup_headings": []}

    text = data.decode("utf-8", errors="replace")
    fm, _, _ = md.split_frontmatter(text)
    headings = md.parse_headings(text)
    links = md.parse_links(text)
    tags = fm.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    conn.execute(
        """INSERT INTO files(path, kind, size, mtime_ns, sha256, title, about,
                             tags, frontmatter, indexed_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(path) DO UPDATE SET
             kind=excluded.kind, size=excluded.size, mtime_ns=excluded.mtime_ns,
             sha256=excluded.sha256, title=excluded.title, about=excluded.about,
             tags=excluded.tags, frontmatter=excluded.frontmatter,
             indexed_at=excluded.indexed_at""",
        (rel, kind, st.st_size, st.st_mtime_ns, sha,
         md.title_of(text, fm, Path(rel).stem), fm.get("about"),
         json.dumps(tags), json.dumps(fm, default=str), now_iso()),
    )

    conn.execute("DELETE FROM headings WHERE path = ?", (rel,))
    conn.executemany(
        """INSERT INTO headings(path, heading_path, slug_path, text, level,
                                byte_start, byte_end, line_start, line_end,
                                content_sha, ordinal)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        [(rel, h.heading_path, h.slug_path, h.text, h.level, h.byte_start,
          h.byte_end, h.line_start, h.line_end, h.content_sha, h.ordinal)
         for h in headings],
    )

    conn.execute("DELETE FROM links WHERE src_path = ?", (rel,))
    unresolved = 0
    rows = []
    for lk in links:
        target = resolve_link(ws, conn, rel, lk.target)
        if target is None:
            unresolved += 1
        rows.append((rel, lk.line, lk.raw, target, lk.anchor, lk.kind, int(target is not None)))
    conn.executemany(
        """INSERT INTO links(src_path, src_line, raw, target_path, target_anchor,
                             kind, resolved) VALUES(?,?,?,?,?,?,?)""",
        rows,
    )

    n_chunks = reindex_chunks(ws, conn, rel, text, headings)

    dups = _dup_headings(headings)
    return {"path": rel, "kind": kind, "size": st.st_size, "headings": len(headings),
            "links": len(links), "unresolved": unresolved, "chunks": n_chunks,
            "dup_headings": dups}


def reindex_chunks(ws, conn, rel: str, text: str, headings) -> int:
    """Rewrite this path's chunks, preserving dirty=0 where embed_sha is unchanged."""
    cfg = ws.config
    prefix = cfg["embed"].get("prefix_doc", "")
    model = cfg["embed"].get("model", "")
    have = {r["embed_sha"] for r in conn.execute("SELECT embed_sha FROM embeddings")}

    old_rowids = [r["rowid"] for r in conn.execute("SELECT rowid FROM chunks WHERE path = ?", (rel,))]
    for rid in old_rowids:
        conn.execute(
            "INSERT INTO chunks_fts(chunks_fts, rowid, text) "
            "SELECT 'delete', rowid, text FROM chunks WHERE rowid = ?", (rid,)
        )
    conn.execute("DELETE FROM chunks WHERE path = ?", (rel,))

    chunks = chunk_document(rel, text, cfg["chunk"], headings)
    for c in chunks:
        esha = c.embed_sha(prefix, model)
        cid = f"{rel}#{c.ordinal}:{c.raw_sha[:12]}"
        cur = conn.execute(
            """INSERT INTO chunks(chunk_id, path, heading_path, ordinal, byte_start,
                                  byte_end, line_start, line_end, text, raw_sha,
                                  embed_sha, dirty)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (cid, rel, c.heading_path, c.ordinal, c.byte_start, c.byte_end,
             c.line_start, c.line_end, c.text, c.raw_sha, esha,
             0 if esha in have else 1),
        )
        conn.execute(
            "INSERT INTO chunks_fts(rowid, text) VALUES(?, ?)", (cur.lastrowid, c.text)
        )
    return len(chunks)


def remove(conn: sqlite3.Connection, rel: str) -> None:
    for r in conn.execute("SELECT rowid, text FROM chunks WHERE path = ?", (rel,)):
        conn.execute(
            "INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', ?, ?)",
            (r["rowid"], r["text"]),
        )
    conn.execute("DELETE FROM chunks   WHERE path = ?", (rel,))
    conn.execute("DELETE FROM headings WHERE path = ?", (rel,))
    conn.execute("DELETE FROM links    WHERE src_path = ?", (rel,))
    conn.execute("DELETE FROM tables   WHERE db_path = ?", (rel,))
    conn.execute("DELETE FROM files    WHERE path = ?", (rel,))
    conn.execute(
        "UPDATE links SET target_path = NULL, resolved = 0 WHERE target_path = ?", (rel,)
    )


def resolve_link(ws: Workspace, conn: sqlite3.Connection, src: str, target: str) -> str | None:
    """exact path -> unique basename -> frontmatter alias -> None (plan.md §9)."""
    target = target.strip().lstrip("./")
    cands = [target]
    if not target.lower().endswith((".md", ".markdown", ".db")):
        cands.append(target + ".md")

    src_dir = str(Path(src).parent)
    for c in cands:
        for base in ([src_dir] if src_dir not in ("", ".") else []) + [""]:
            rel = (Path(base) / c).as_posix() if base else c
            rel = Path(rel).as_posix()
            if ws.abs(rel).exists():
                return rel
            row = conn.execute("SELECT path FROM files WHERE path = ?", (rel,)).fetchone()
            if row:
                return row["path"]

    stem = Path(target).name
    stems = [stem, stem + ".md"] if not stem.lower().endswith(".md") else [stem]
    hits = [
        r["path"]
        for r in conn.execute("SELECT path FROM files")
        if Path(r["path"]).name in stems
    ]
    if len(hits) == 1:
        return hits[0]

    for r in conn.execute("SELECT path, frontmatter FROM files"):
        try:
            fm = json.loads(r["frontmatter"] or "{}")
        except json.JSONDecodeError:
            continue
        aliases = fm.get("aliases") or fm.get("alias") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        if target in aliases or Path(target).stem in aliases:
            return r["path"]
    return None


def reresolve_incoming(ws: Workspace, conn: sqlite3.Connection) -> int:
    """Re-run resolution for every unresolved link. Cheap; run after new/mv."""
    fixed = 0
    for r in conn.execute("SELECT rowid, src_path, raw, target_anchor, kind FROM links WHERE resolved = 0"):
        links = md.parse_links(r["raw"])
        if not links:
            continue
        tgt = resolve_link(ws, conn, r["src_path"], links[0].target)
        if tgt:
            conn.execute(
                "UPDATE links SET target_path = ?, resolved = 1 WHERE rowid = ?",
                (tgt, r["rowid"]),
            )
            fixed += 1
    return fixed


def _dup_headings(headings) -> list[str]:
    seen: dict[str, int] = {}
    for h in headings:
        seen[h.heading_path] = seen.get(h.heading_path, 0) + 1
    return sorted(k for k, v in seen.items() if v > 1)


def backlinks(conn: sqlite3.Connection, rel: str) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT src_path, src_line, raw FROM links WHERE target_path = ? "
            "AND src_path != ? ORDER BY src_path, src_line",
            (rel, rel),
        )
    )


def stale_check(ws: Workspace, conn: sqlite3.Connection, rel: str) -> bool:
    """True if the file on disk no longer matches the registry (emit W_STALE)."""
    row = conn.execute("SELECT size, mtime_ns FROM files WHERE path = ?", (rel,)).fetchone()
    p = ws.abs(rel)
    if row is None or not p.exists():
        return False
    st = p.stat()
    return st.st_size != row["size"] or st.st_mtime_ns != row["mtime_ns"]
