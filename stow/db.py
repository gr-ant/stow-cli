"""SQLite index (plan.md §5).

WAL mode: readers never block the writer, the writer never blocks readers.
That is the whole reason the index is SQLite and not DuckDB — it deletes the
lock file and makes E_LOCKED a pathology rather than an expected outcome.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS files (
    path        TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    size        INTEGER NOT NULL DEFAULT 0,
    mtime_ns    INTEGER NOT NULL DEFAULT 0,
    sha256      TEXT,
    title       TEXT,
    about       TEXT,
    tags        TEXT NOT NULL DEFAULT '[]',      -- JSON array
    frontmatter TEXT NOT NULL DEFAULT '{}',      -- JSON object
    indexed_at  TEXT
);

CREATE TABLE IF NOT EXISTS headings (
    path         TEXT NOT NULL,
    heading_path TEXT NOT NULL,   -- display form: 'Chunking/Overlap'
    slug_path    TEXT NOT NULL,   -- match form:   'chunking/overlap'
    text         TEXT NOT NULL,
    level        INTEGER NOT NULL,
    byte_start   INTEGER NOT NULL,
    byte_end     INTEGER NOT NULL,
    line_start   INTEGER NOT NULL,
    line_end     INTEGER NOT NULL,
    content_sha  TEXT NOT NULL,
    ordinal      INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (path) REFERENCES files(path) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS headings_path     ON headings(path);
CREATE INDEX IF NOT EXISTS headings_slug     ON headings(slug_path);

CREATE TABLE IF NOT EXISTS links (
    src_path      TEXT NOT NULL,
    src_line      INTEGER NOT NULL,
    raw           TEXT NOT NULL,
    target_path   TEXT,
    target_anchor TEXT,
    kind          TEXT NOT NULL,   -- 'wiki' | 'md'
    resolved      INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (src_path) REFERENCES files(path) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS links_src ON links(src_path);
CREATE INDEX IF NOT EXISTS links_tgt ON links(target_path);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id     TEXT PRIMARY KEY,
    path         TEXT NOT NULL,
    heading_path TEXT NOT NULL DEFAULT '',
    ordinal      INTEGER NOT NULL DEFAULT 0,
    byte_start   INTEGER NOT NULL,
    byte_end     INTEGER NOT NULL,
    line_start   INTEGER NOT NULL DEFAULT 0,
    line_end     INTEGER NOT NULL DEFAULT 0,
    text         TEXT NOT NULL,
    raw_sha      TEXT NOT NULL,    -- sha of the body alone: identity
    embed_sha    TEXT NOT NULL,    -- sha of the exact embedder input: cache validity
    dirty        INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (path) REFERENCES files(path) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS chunks_path  ON chunks(path);
CREATE INDEX IF NOT EXISTS chunks_dirty ON chunks(dirty);
CREATE INDEX IF NOT EXISTS chunks_embed ON chunks(embed_sha);

CREATE TABLE IF NOT EXISTS embeddings (
    embed_sha TEXT PRIMARY KEY,
    model     TEXT NOT NULL,
    dim       INTEGER NOT NULL,
    vec       BLOB NOT NULL        -- float32 little-endian
);

CREATE TABLE IF NOT EXISTS tables (
    db_path    TEXT NOT NULL,
    table_name TEXT NOT NULL,
    row_count  INTEGER NOT NULL DEFAULT 0,
    columns    TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (db_path, table_name)
);

CREATE TABLE IF NOT EXISTS versions (
    sha        TEXT NOT NULL,
    path       TEXT NOT NULL,
    size       INTEGER NOT NULL,
    command    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    seq        INTEGER PRIMARY KEY AUTOINCREMENT
);
CREATE INDEX IF NOT EXISTS versions_path ON versions(path, seq DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text,
    content='chunks',
    content_rowid='rowid'
);
"""


def connect(index_path: Path, *, create: bool = False) -> sqlite3.Connection:
    """Open the index with the pragmas that make concurrency work."""
    index_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(index_path), timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if create:
        ensure_schema(conn)
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )


def fts_reindex(conn: sqlite3.Connection) -> None:
    """Rebuild the external-content FTS index from chunks."""
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")


class tx:
    """Write transaction context manager. `with tx(conn): ...`"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def __enter__(self) -> sqlite3.Connection:
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self.conn.execute("COMMIT")
        else:
            self.conn.execute("ROLLBACK")
        return False
