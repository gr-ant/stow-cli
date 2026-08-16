"""Chunking (plan.md §7). The critical invariant: the embedded string carries
the heading path only, never the file path — that's what makes `mv` free
(plan.md §5)."""

from __future__ import annotations

from stow.chunker import chunk_document, embed_input
from stow.md import parse_headings

CFG = {"max_chars": 400, "min_chars": 80, "overlap": 0.2}


def _doc(*sections: tuple[str, str]) -> str:
    """Build a document from (heading, body) H2 pairs under one H1 title."""
    parts = ["# Title\n"]
    for heading, body in sections:
        parts.append(f"## {heading}\n\n{body}\n")
    return "\n".join(parts)


# --------------------------------------------------------------------------
# the heading-path-only invariant (plan.md §5)
# --------------------------------------------------------------------------
def test_embed_input_has_no_file_path():
    text = embed_input("Chunking/Overlap", "some body", "passage: ")
    assert "notes/rag.md" not in text
    assert "/" not in text.split("\n\n")[0].replace(" > ", "")  # no raw slash outside the " > " join
    assert text.startswith("passage: Chunking > Overlap")


def test_mv_is_free_same_content_different_path_same_embed_sha():
    body = "x" * 300
    text = _doc(("Chunking", body))
    headings = parse_headings(text)
    a = chunk_document("notes/rag.md", text, CFG, headings)
    b = chunk_document("research/rag.md", text, CFG, headings)
    assert len(a) == len(b) >= 1
    for ca, cb in zip(a, b):
        assert ca.embed_sha() == cb.embed_sha()
        assert ca.raw_sha == cb.raw_sha


def test_rename_heading_changes_embed_sha_but_not_raw_sha():
    body = "x" * 300
    text_a = _doc(("Chunking", body))
    text_b = _doc(("Overlap", body))
    ca = chunk_document("notes/rag.md", text_a, CFG, parse_headings(text_a))
    cb = chunk_document("notes/rag.md", text_b, CFG, parse_headings(text_b))
    assert ca[0].embed_sha() != cb[0].embed_sha()
    # raw_sha is body identity only — renaming the heading doesn't touch it.
    assert ca[0].raw_sha == cb[0].raw_sha


def test_reordering_headings_is_free():
    text_a = _doc(("Alpha", "a" * 200), ("Beta", "b" * 200))
    text_b = _doc(("Beta", "b" * 200), ("Alpha", "a" * 200))
    ca = {c.heading_path: c for c in chunk_document("n.md", text_a, CFG, parse_headings(text_a))}
    cb = {c.heading_path: c for c in chunk_document("n.md", text_b, CFG, parse_headings(text_b))}
    assert ca.keys() == cb.keys()
    for hp in ca:
        assert ca[hp].embed_sha() == cb[hp].embed_sha()


# --------------------------------------------------------------------------
# merge direction (plan.md §7 step 3): forward, not backward
# --------------------------------------------------------------------------
def test_short_section_merges_forward_into_next_sibling():
    text = _doc(("Intro", "short."), ("Chunking", "y" * 150))
    chunks = chunk_document("n.md", text, CFG, parse_headings(text))
    assert len(chunks) == 1
    # The merged chunk keeps the FIRST unit's heading path, so the location
    # `find` prints points at where the chunk actually starts.
    assert chunks[0].heading_path == "Intro"
    assert "short." in chunks[0].text
    assert "y" * 150 in chunks[0].text


def test_trailing_short_section_has_nowhere_to_merge_forward():
    text = _doc(("Chunking", "y" * 150), ("Tail", "short."))
    chunks = chunk_document("n.md", text, CFG, parse_headings(text))
    assert len(chunks) == 2
    assert chunks[0].heading_path == "Chunking"
    assert chunks[1].heading_path == "Tail"
    assert chunks[1].text.strip() == "short."


def test_merge_forward_does_not_exceed_max_chars():
    cfg = {"max_chars": 100, "min_chars": 90, "overlap": 0.0}
    text = _doc(("A", "a" * 50), ("B", "b" * 80))
    chunks = chunk_document("n.md", text, cfg, parse_headings(text))
    # 50 + 2 + 80 = 132 > max_chars(100): merging forward would overflow, so
    # the short section A must NOT merge into B and stays on its own.
    assert [c.heading_path for c in chunks] == ["A", "B"]


# --------------------------------------------------------------------------
# line numbers populated from byte offsets
# --------------------------------------------------------------------------
def test_line_numbers_are_populated_and_correct():
    text = _doc(("Chunking", "line one\nline two\nline three"))
    chunks = chunk_document("n.md", text, CFG, parse_headings(text))
    assert len(chunks) == 1
    c = chunks[0]
    assert c.line_start > 0
    assert c.line_end >= c.line_start
    doc_lines = text.split("\n")
    covered = "\n".join(doc_lines[c.line_start - 1 : c.line_end])
    assert "line one" in covered
    assert "line three" in covered


def test_byte_offsets_are_exact_substrings_of_the_document():
    text = _doc(("Chunking", "alpha beta gamma delta " * 20))
    chunks = chunk_document("n.md", text, CFG, parse_headings(text))
    data = text.encode("utf-8")
    assert chunks  # sanity
    for c in chunks:
        assert data[c.byte_start : c.byte_end].decode("utf-8") == c.text


# --------------------------------------------------------------------------
# max_chars split + overlap carry
# --------------------------------------------------------------------------
def test_max_chars_is_respected_and_overlap_carries_the_tail():
    cfg = {"max_chars": 200, "min_chars": 10, "overlap": 0.25}
    paras = [f"paragraph {i} " + ("word " * 15) for i in range(6)]
    body = "\n\n".join(paras)
    text = _doc(("Chunking", body))
    chunks = chunk_document("n.md", text, cfg, parse_headings(text))
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.text) <= cfg["max_chars"]
    # The tail of piece N (an `overlap` fraction of it) reappears at the start
    # of piece N+1.
    tail = chunks[0].text[-int(cfg["max_chars"] * cfg["overlap"]) :]
    assert tail[-15:] in chunks[1].text


def test_min_chars_prevents_unnecessary_tiny_chunks():
    cfg = {"max_chars": 1000, "min_chars": 200, "overlap": 0.15}
    text = _doc(("A", "short a."), ("B", "short b."), ("C", "z" * 500))
    chunks = chunk_document("n.md", text, cfg, parse_headings(text))
    # A and B are both short and merge forward into C (combined still under
    # max_chars), leaving a single chunk labelled by where it starts.
    assert len(chunks) == 1
    assert chunks[0].heading_path == "A"
    assert "short a." in chunks[0].text
    assert "short b." in chunks[0].text


def test_merge_never_crosses_a_parent_boundary():
    """A short H3 must not fold into the next H2.

    A chunk spanning two unrelated top-level sections gets a heading prefix that
    describes neither, which destroys the retrievability that prefix exists to
    provide (plan.md §7 step 4).
    """
    text = (
        "# T\n\n## Chunking\n\n### Overlap\n\nshort a.\n\n"
        "### Sizing\n\nshort b.\n\n## Evaluation\n\nshort c.\n"
    )
    chunks = chunk_document("n.md", text, CFG, parse_headings(text))
    paths = [c.heading_path for c in chunks]
    # Overlap+Sizing are siblings and merge; Evaluation has a different parent.
    assert paths == ["Chunking/Overlap", "Evaluation"]
    assert "short a." in chunks[0].text and "short b." in chunks[0].text
    assert "short c." in chunks[1].text
