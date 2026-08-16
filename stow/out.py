"""Output. Every byte printed is context the agent pays for (plan.md §10).

Human mode is terse by default; --json switches every command to a single JSON
document on stdout. Warnings (W_*) always go to stderr so they never pollute a
parsed payload.
"""

from __future__ import annotations

import json
import sys

_json_mode = False
_quiet_warnings = False


def set_json(on: bool) -> None:
    global _json_mode
    _json_mode = on


def is_json() -> bool:
    return _json_mode


def emit(human: str = "", data=None) -> None:
    """Print `data` as JSON in --json mode, else the human string."""
    if _json_mode:
        if data is not None:
            print(json.dumps(data, ensure_ascii=False, default=str))
    elif human:
        print(human)


def line(s: str = "") -> None:
    if not _json_mode:
        print(s)


def raw(s: str) -> None:
    """Content passthrough (file bodies, SQL results) — never JSON-wrapped."""
    sys.stdout.write(s)


def warn(code: str, message: str, hint: str | None = None) -> None:
    if _quiet_warnings:
        return
    msg = f"{code}: {message}"
    if hint:
        msg += f" {hint}"
    print(msg, file=sys.stderr)


def error(text: str) -> None:
    print(text, file=sys.stderr)


def fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n}b"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}k"
    return f"{n / 1024 / 1024:.1f}M"


def fmt_count(n: int, singular: str, plural: str | None = None) -> str:
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def excerpt(text: str, lines: int = 2, width: int = 100) -> list[str]:
    """The two-line preview `find` prints under a hit."""
    out = []
    for ln in [l.strip() for l in text.split("\n") if l.strip()][:lines]:
        out.append(ln if len(ln) <= width else ln[: width - 1] + "…")
    return out
