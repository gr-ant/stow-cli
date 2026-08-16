"""stw find QUERY — hybrid semantic + BM25 search (plan.md §8).

Hybrid by default: exact identifiers and error strings are what BM25 nails
and embeddings miss, and an agent's queries are full of them. Fusion is
reciprocal rank fusion (Σ 1/(60+rank)) so the two incomparable scales never
need normalizing against each other.

`find` embeds at most `embed.max_inline` dirty chunks before searching
(oldest-dirty first, batch-committing) and never blocks on a large backfill —
if chunks are still dirty afterward it answers anyway and warns on stderr.
"""

from __future__ import annotations

import argparse
import json
import re

from .. import out
from ..embedder import embed_dirty, embed_query
from ..errors import EmbedFailed, NoEmbedder
from ..index import stale_check
from ..vectors import cosine_topk

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("query")
    parser.add_argument("-k", type=int, default=10, help="number of hits (default 10)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--vector-only", action="store_true")
    group.add_argument("--text-only", action="store_true")
    parser.add_argument("--under", default=None, help="restrict to files under this path")
    parser.add_argument("--tag", default=None, help="restrict to files carrying this tag")
    parser.add_argument("--full", action="store_true", help="print whole chunks, not excerpts")


def _sanitize_fts(query: str) -> str:
    """Quote every token so FTS5 operator syntax in the raw query (quotes,
    parens, NEAR, column filters, ...) can never raise a syntax error."""
    tokens = _TOKEN_RE.findall(query)
    return " ".join(f'"{t}"' for t in tokens)


def _rrf(rank_lists: list[list[str]], k0: int = 60) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranks in rank_lists:
        for i, chunk_id in enumerate(ranks):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k0 + i + 1)
    return scores


def run(ws, conn, args) -> int:
    k = max(1, args.k)
    text_only = bool(args.text_only)
    vector_only = bool(args.vector_only)
    cfg = ws.config["embed"]

    # -- inline backfill: bounded, batch-committing, never a hard failure ---
    if not text_only:
        cmd = cfg.get("cmd") or []
        if not cmd:
            text_only = True
            out.warn("W_NO_EMBEDDER", "no embedder configured.", "Falling back to --text-only.")
        else:
            max_inline = int(cfg.get("max_inline", 256))
            try:
                embed_dirty(ws, conn, limit=max_inline)
            except (NoEmbedder, EmbedFailed):
                pass  # a broken/absent sidecar must not stall `find`

    if not text_only:
        total_embeddings = conn.execute("SELECT COUNT(*) n FROM embeddings").fetchone()["n"]
        if total_embeddings == 0:
            text_only = True
            out.warn("W_NO_EMBEDDER", "no embeddings available.", "Falling back to --text-only.")

    # -- text leg (BM25) ------------------------------------------------
    text_ranks: list[str] = []
    if not vector_only:
        fts_q = _sanitize_fts(args.query)
        if fts_q:
            rows = conn.execute(
                "SELECT c.chunk_id FROM chunks_fts "
                "JOIN chunks c ON c.rowid = chunks_fts.rowid "
                "WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?",
                (fts_q, max(k * 5, 50)),
            ).fetchall()
            text_ranks = [r["chunk_id"] for r in rows]

    # -- vector leg (brute force cosine) ---------------------------------
    vector_ranks: list[str] = []
    if not text_only:
        qvec = None
        try:
            qvec = embed_query(ws, args.query)
        except EmbedFailed:
            qvec = None  # a hiccup embedding the query must not fail `find`
        if qvec is not None:
            cand_rows = conn.execute(
                "SELECT c.chunk_id, e.vec FROM chunks c "
                "JOIN embeddings e ON e.embed_sha = c.embed_sha"
            ).fetchall()
            candidates = [(r["chunk_id"], r["vec"]) for r in cand_rows]
            scored = cosine_topk(qvec, candidates, max(k * 5, 50))
            vector_ranks = [chunk_id for chunk_id, _ in scored]

    rank_lists = [lst for lst in (text_ranks, vector_ranks) if lst]
    fused = sorted(_rrf(rank_lists).items(), key=lambda t: -t[1])

    under = ws.rel(args.under) if args.under else None

    hits: list[dict] = []
    if fused:
        ids = [chunk_id for chunk_id, _ in fused]
        placeholders = ",".join("?" for _ in ids)
        details = {
            r["chunk_id"]: r
            for r in conn.execute(
                f"SELECT c.chunk_id, c.path, c.heading_path, c.line_start, c.line_end, "
                f"c.text, f.tags FROM chunks c JOIN files f ON f.path = c.path "
                f"WHERE c.chunk_id IN ({placeholders})",
                ids,
            ).fetchall()
        }
        seen_paths: set[str] = set()
        for chunk_id, score in fused:
            r = details.get(chunk_id)
            if r is None:
                continue
            if under is not None and r["path"] != under and not r["path"].startswith(under + "/"):
                continue
            if args.tag:
                try:
                    tags = json.loads(r["tags"] or "[]")
                except json.JSONDecodeError:
                    tags = []
                if args.tag not in tags:
                    continue
            hits.append(
                {
                    "score": round(score, 4),
                    "path": r["path"],
                    "heading_path": r["heading_path"],
                    "line_start": r["line_start"],
                    "line_end": r["line_end"],
                    "text": r["text"],
                }
            )
            seen_paths.add(r["path"])
            if len(hits) >= k:
                break

        for p in seen_paths:
            if stale_check(ws, conn, p):
                out.warn("W_STALE", f"{p} changed on disk since it was indexed", "Run `stw sync`.")

    lines: list[str] = []
    for h in hits:
        loc = f"{h['path']}#{h['heading_path']}" if h["heading_path"] else h["path"]
        lines.append(f"{h['score']:.3f}  {loc}  L{h['line_start']}-{h['line_end']}")
        body_lines = h["text"].splitlines() if args.full else out.excerpt(h["text"])
        for ln in body_lines:
            lines.append(f"      {ln}")
    human = "\n".join(lines) if lines else "no matches"

    remaining = conn.execute("SELECT COUNT(*) n FROM chunks WHERE dirty = 1").fetchone()["n"]
    if remaining:
        out.warn("W_DIRTY", f"{remaining} chunks pending.", "Run `stw embed`.")

    out.emit(human, {"query": args.query, "k": k, "hits": hits})
    return 0
