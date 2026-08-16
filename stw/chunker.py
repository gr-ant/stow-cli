"""Chunking (plan.md §7).

Boundaries follow the heading tree. The embedded text is prefixed with the
HEADING PATH ONLY — never the file path. That is what makes `mv` free (§5).
"""

from __future__ import annotations

from dataclasses import dataclass

from .hashing import sha256_text
from .md import Heading, parse_headings


@dataclass
class Chunk:
    path: str
    heading_path: str
    ordinal: int
    text: str
    byte_start: int
    byte_end: int
    line_start: int
    line_end: int

    @property
    def raw_sha(self) -> str:
        return sha256_text(self.text)

    def embed_input(self, prefix_doc: str = "") -> str:
        return embed_input(self.heading_path, self.text, prefix_doc)

    def embed_sha(self, prefix_doc: str = "", model: str = "") -> str:
        return sha256_text(f"{model}\n{self.embed_input(prefix_doc)}")


def embed_input(heading_path: str, text: str, prefix_doc: str = "") -> str:
    """The exact string handed to the embedder.

    Heading path only. No file path — see plan.md §5 'The two hashes'.
    """
    head = heading_path.replace("/", " > ") if heading_path else ""
    body = text.strip()
    return f"{prefix_doc}{head}\n\n{body}" if head else f"{prefix_doc}{body}"


def chunk_document(path: str, text: str, cfg: dict, headings: list[Heading] | None = None) -> list[Chunk]:
    """Split a document into chunks (plan.md §7).

    1. split at H2/H3 sections
    2. over max_chars -> split on paragraph breaks carrying `overlap`
    3. under min_chars -> merge forward into the next sibling
    4. heading path prefix happens in embed_input(), not here
    """
    max_chars = int(cfg.get("max_chars", 1200))
    min_chars = int(cfg.get("min_chars", 200))
    overlap = float(cfg.get("overlap", 0.15))
    headings = headings if headings is not None else parse_headings(text)
    data = text.encode("utf-8")

    units: list[tuple[str, str, int, int]] = []  # (heading_path, body, byte_start, byte_end)
    leaves = [h for h in headings if h.level >= 2]
    if not leaves:
        body = text.strip()
        if body:
            units.append(("", body, 0, len(data)))
    else:
        for h in leaves:
            # A parent section owns only the text before its first child, or the
            # child's text would be indexed twice.
            end = h.byte_end
            for nxt in headings[headings.index(h) + 1 :]:
                if nxt.level > h.level and nxt.byte_start < end:
                    end = nxt.byte_start
                    break
                if nxt.level <= h.level:
                    break
            body = data[h.body_byte_start : end].decode("utf-8").strip("\n")
            if body.strip():
                units.append((h.heading_path, body, h.body_byte_start, end))

    # Step 3: a section under min_chars has no business standing alone — fold
    # it forward into the next sibling rather than back into the one already
    # emitted (plan.md §7: "merge forward into the next sibling").
    #
    # Two constraints keep the merge honest. Only true siblings merge — folding
    # across a parent boundary produces a chunk spanning unrelated topics whose
    # heading prefix describes neither, which is exactly the retrievability the
    # prefix exists to provide (§7 step 4). And the merged chunk keeps the FIRST
    # unit's heading path, so the label `find` prints points at where the chunk
    # actually starts. A trailing short section with no sibling to merge into
    # just stays undersized.
    merged: list[tuple[str, str, int, int]] = []
    i = 0
    n = len(units)
    while i < n:
        hp, body, bs, be = units[i]
        while len(body) < min_chars and i + 1 < n:
            nhp, nbody, _nbs, nbe = units[i + 1]
            if _parent(nhp) != _parent(hp):
                break
            if len(body) + 2 + len(nbody) > max_chars:
                break
            body = body + "\n\n" + nbody
            be = nbe
            i += 1
        merged.append((hp, body, bs, be))
        i += 1

    out: list[Chunk] = []
    for hp, body, bs, be in merged:
        cursor = bs
        for piece in _split(body, max_chars, overlap):
            piece_bytes = piece.encode("utf-8")
            idx = data.find(piece_bytes, cursor, be)
            if idx == -1:
                idx = data.find(piece_bytes, bs, be)
            if idx == -1:
                # Shouldn't happen (pieces are always literal substrings of the
                # unit they came from), but fall back to the unit's own extent
                # rather than crash.
                p_start, p_end = bs, be
            else:
                p_start, p_end = idx, idx + len(piece_bytes)
                cursor = p_end
            line_start = data.count(b"\n", 0, p_start) + 1
            line_end = data.count(b"\n", 0, max(p_end - 1, p_start)) + 1
            out.append(
                Chunk(
                    path=path,
                    heading_path=hp,
                    ordinal=len(out),
                    text=piece,
                    byte_start=p_start,
                    byte_end=p_end,
                    line_start=line_start,
                    line_end=line_end,
                )
            )
    return out


def _parent(heading_path: str) -> str:
    """'Chunking/Overlap' -> 'Chunking'. Top-level sections share the '' parent."""
    return heading_path.rsplit("/", 1)[0] if "/" in heading_path else ""


def _split(body: str, max_chars: int, overlap: float) -> list[str]:
    if len(body) <= max_chars:
        return [body]
    paras = [p for p in body.split("\n\n") if p.strip()]
    out: list[str] = []
    cur = ""
    for p in paras:
        if cur and len(cur) + len(p) + 2 > max_chars:
            out.append(cur)
            tail = cur[-int(max_chars * overlap) :] if overlap > 0 else ""
            cur = (tail + "\n\n" + p) if tail else p
        else:
            cur = (cur + "\n\n" + p) if cur else p
    if cur.strip():
        out.append(cur)
    return out
