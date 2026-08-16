"""Markdown parsing: frontmatter, heading tree, links, section addressing.

Shared by every command that touches a .md file. Offsets are byte offsets into
the utf-8 encoding of the whole document (frontmatter included); line numbers
are 1-based over the whole document.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .errors import AmbiguousHeading, NoSuchHeading
from .hashing import sha256_text

FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
ATX_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
WIKI_RE = re.compile(r"\[\[([^\]\|#]+)(?:#([^\]\|]+))?(?:\|([^\]]+))?\]\]")
MD_LINK_RE = re.compile(r"(?<!\!)\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
INLINE_CODE_RE = re.compile(r"`[^`]*`")
EXTERNAL_RE = re.compile(r"^(https?|mailto|ftp|tel):", re.I)


# --------------------------------------------------------------------------
# frontmatter
# --------------------------------------------------------------------------
def split_frontmatter(text: str) -> tuple[dict, str, int]:
    """Return (frontmatter, body, body_line_offset).

    body_line_offset is how many lines precede the body, so a body line index i
    maps to document line body_line_offset + i + 1.
    """
    if not text.startswith("---"):
        return {}, text, 0
    lines = text.split("\n")
    if lines[0].strip() != "---":
        return {}, text, 0
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            fm = parse_frontmatter("\n".join(lines[1:i]))
            body = "\n".join(lines[i + 1 :])
            return fm, body, i + 1
    return {}, text, 0


def parse_frontmatter(block: str) -> dict:
    """Flat YAML subset: `key: scalar`, `key: [a, b]`, and `- item` lists."""
    out: dict = {}
    key: str | None = None
    for raw in block.split("\n"):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.lstrip().startswith("- ") and key is not None:
            out.setdefault(key, [])
            if isinstance(out[key], list):
                out[key].append(_scalar(line.lstrip()[2:].strip()))
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        key = k.strip()
        v = v.strip()
        if v == "":
            out[key] = []
        elif v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            out[key] = [_scalar(x.strip()) for x in inner.split(",") if x.strip()] if inner else []
        else:
            out[key] = _scalar(v)
    return out


def _scalar(v: str):
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    low = v.lower()
    if low in ("true", "false"):
        return low == "true"
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    return v


def render_frontmatter(fm: dict) -> str:
    """Emit the flat YAML subset parse_frontmatter reads back."""
    lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, list):
            lines.append(f"{k}: [{', '.join(_quote(str(x)) for x in v)}]")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        elif isinstance(v, (int, float)):
            lines.append(f"{k}: {v}")
        else:
            lines.append(f"{k}: {_quote(str(v))}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _quote(s: str) -> str:
    if s == "" or re.search(r'[:#\[\]{},"\']', s) or s.strip() != s:
        return '"' + s.replace('"', '\\"') + '"'
    return s


def ensure_frontmatter(text: str, defaults: dict) -> str:
    """Inject frontmatter if absent; fill missing default keys if present."""
    fm, body, _ = split_frontmatter(text)
    merged = {**defaults, **fm}
    if fm == merged and text.startswith("---"):
        return text
    return render_frontmatter(merged) + body.lstrip("\n")


# --------------------------------------------------------------------------
# headings
# --------------------------------------------------------------------------
def slugify(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[`*_~]", "", s)
    s = re.sub(r"\[\[([^\]]+)\]\]", r"\1", s)
    s = re.sub(r"[^\w\s/-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    return s.strip("-")


def slug_path(parts: list[str]) -> str:
    return "/".join(slugify(p) for p in parts)


@dataclass
class Heading:
    text: str
    level: int
    heading_path: str      # display: 'Chunking/Overlap'
    slug_path: str         # match:   'chunking/overlap'
    line_start: int        # 1-based, the heading line
    line_end: int          # 1-based, last line of the section
    byte_start: int        # start of the heading line
    byte_end: int          # end of section (exclusive)
    body_byte_start: int   # first byte after the heading line
    ordinal: int = 0
    content_sha: str = ""


def _fence_mask(lines: list[str]) -> list[bool]:
    """True where a line is inside a fenced code block."""
    inside = False
    marker = ""
    mask = []
    for ln in lines:
        m = FENCE_RE.match(ln)
        if m:
            tok = m.group(1)
            if not inside:
                inside, marker = True, tok[0] * 3
                mask.append(True)
                continue
            if tok[0] * 3 == marker:
                inside = False
                mask.append(True)
                continue
        mask.append(inside)
    return mask


def parse_headings(text: str) -> list[Heading]:
    """Heading tree with byte/line extents. H1 is the title and never an ancestor."""
    lines = text.split("\n")
    mask = _fence_mask(lines)
    offsets: list[int] = []
    off = 0
    for ln in lines:
        offsets.append(off)
        off += len(ln.encode("utf-8")) + 1
    total = len(text.encode("utf-8"))

    raw: list[Heading] = []
    stack: list[tuple[int, str]] = []   # (level, display text) for levels >= 2
    for i, ln in enumerate(lines):
        if mask[i]:
            continue
        m = ATX_RE.match(ln)
        if not m:
            continue
        level = len(m.group(1))
        title = m.group(2).strip()
        if not title:
            continue
        if level >= 2:
            while stack and stack[-1][0] >= level:
                stack.pop()
            parts = [t for _, t in stack] + [title]
            stack.append((level, title))
        else:
            stack.clear()
            parts = [title]
        raw.append(
            Heading(
                text=title,
                level=level,
                heading_path="/".join(parts),
                slug_path=slug_path(parts),
                line_start=i + 1,
                line_end=i + 1,
                byte_start=offsets[i],
                byte_end=total,
                body_byte_start=min(offsets[i] + len(ln.encode("utf-8")) + 1, total),
                ordinal=0,
            )
        )

    # close each section at the next heading of equal-or-shallower level
    for idx, h in enumerate(raw):
        for nxt in raw[idx + 1 :]:
            if nxt.level <= h.level:
                h.byte_end = nxt.byte_start
                h.line_end = nxt.line_start - 1
                break
        else:
            h.byte_end = total
            h.line_end = len(lines)
        h.content_sha = sha256_text(section_body(text, h))

    seen: dict[str, int] = {}
    for h in raw:
        h.ordinal = seen.get(h.slug_path, 0)
        seen[h.slug_path] = h.ordinal + 1
    return raw


def section_body(text: str, h: Heading) -> str:
    """The section's body, heading line excluded, trailing blank lines trimmed."""
    data = text.encode("utf-8")
    return data[h.body_byte_start : h.byte_end].decode("utf-8").strip("\n")


def resolve_section(path: str, headings: list[Heading], addr: str) -> Heading:
    """Match a '#Chunking/Overlap' address against the heading tree.

    Exact slug path first, then unique suffix match. Ambiguity is an error that
    names both line numbers (plan.md §3).
    """
    want = slug_path([p for p in addr.split("/") if p.strip()])
    exact = [h for h in headings if h.slug_path == want]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise AmbiguousHeading(path, addr, [h.line_start for h in exact])
    suffix = [h for h in headings if h.slug_path.endswith("/" + want) or h.slug_path == want]
    if len(suffix) == 1:
        return suffix[0]
    if len(suffix) > 1:
        raise AmbiguousHeading(path, addr, [h.line_start for h in suffix])
    leaf = want.rsplit("/", 1)[-1]
    byleaf = [h for h in headings if slugify(h.text) == leaf]
    if len(byleaf) == 1:
        return byleaf[0]
    if len(byleaf) > 1:
        raise AmbiguousHeading(path, addr, [h.line_start for h in byleaf])
    raise NoSuchHeading(path, addr)


def replace_section(text: str, h: Heading, new_body: str) -> str:
    """Swap one section's body, preserving the heading line and spacing."""
    data = text.encode("utf-8")
    head = data[: h.body_byte_start].decode("utf-8")
    tail = data[h.byte_end :].decode("utf-8")
    body = new_body.strip("\n")
    stripped = body.lstrip()
    if stripped.startswith("#" * h.level + " "):
        # caller supplied the heading line themselves; drop ours
        head = data[: h.byte_start].decode("utf-8")
    block = "\n" + body + "\n"
    if tail and not block.endswith("\n\n"):
        block += "\n"
    return head + block + tail


def split_address(spec: str) -> tuple[str, str | None]:
    """'notes/rag.md#Chunking/Overlap' -> ('notes/rag.md', 'Chunking/Overlap')."""
    if "#" in spec:
        p, _, anchor = spec.partition("#")
        return p, (anchor or None)
    return spec, None


# --------------------------------------------------------------------------
# links
# --------------------------------------------------------------------------
@dataclass
class Link:
    raw: str
    target: str
    anchor: str | None
    kind: str            # 'wiki' | 'md'
    line: int
    alias: str | None = None


def parse_links(text: str) -> list[Link]:
    """[[wiki]] and [text](path.md) links, skipping code fences and inline code."""
    lines = text.split("\n")
    mask = _fence_mask(lines)
    out: list[Link] = []
    for i, ln in enumerate(lines):
        if mask[i]:
            continue
        clean = INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), ln)
        for m in WIKI_RE.finditer(clean):
            target = m.group(1).strip()
            if not target:
                continue
            out.append(
                Link(
                    raw=m.group(0),
                    target=target,
                    anchor=(m.group(2) or None),
                    kind="wiki",
                    line=i + 1,
                    alias=(m.group(3) or None),
                )
            )
        for m in MD_LINK_RE.finditer(clean):
            href = m.group(2).strip()
            if not href or EXTERNAL_RE.match(href) or href.startswith("#"):
                continue
            target, _, anchor = href.partition("#")
            if not target:
                continue
            out.append(
                Link(
                    raw=m.group(0),
                    target=target,
                    anchor=(anchor or None),
                    kind="md",
                    line=i + 1,
                    alias=(m.group(1) or None),
                )
            )
    return out


def title_of(text: str, fm: dict, fallback: str) -> str:
    if fm.get("title"):
        return str(fm["title"])
    for h in parse_headings(text):
        if h.level == 1:
            return h.text
    return fallback


def json_list(v) -> str:
    return json.dumps(v if isinstance(v, list) else ([] if v in (None, "") else [v]))
