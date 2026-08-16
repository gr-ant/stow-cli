"""Error and warning types.

Every failure the agent can hit is a stwError with a machine-parseable code on
stderr that names the next action (plan.md §11).
"""

from __future__ import annotations


class stwError(Exception):
    """A named, recoverable failure.

    code: E_* identifier, first token on the stderr line.
    message: what happened, concrete (paths, line numbers, hashes).
    hint: the next action, appended as a sentence. Optional but strongly preferred.
    """

    exit_code = 2

    def __init__(self, code: str, message: str, hint: str | None = None) -> None:
        self.code = code
        self.message = message
        self.hint = hint
        super().__init__(f"{code}: {message}")

    def render(self) -> str:
        body = f"{self.code}: {self.message}"
        if self.hint:
            body += f" {self.hint}"
        return body

    def to_json(self) -> dict:
        return {"error": self.code, "message": self.message, "hint": self.hint}


class NoWorkspace(stwError):
    def __init__(self, start: str) -> None:
        super().__init__(
            "E_NO_WORKSPACE",
            f"no .stw/ found at or above {start}",
            "Run `stw init` to create one.",
        )


class NotFound(stwError):
    def __init__(self, path: str) -> None:
        super().__init__("E_NOT_FOUND", f"{path} is not in the workspace")


class AmbiguousHeading(stwError):
    def __init__(self, path: str, heading: str, lines: list[int]) -> None:
        where = ", ".join(f"L{n}" for n in lines)
        super().__init__(
            "E_AMBIGUOUS_HEADING",
            f"'{heading}' appears {len(lines)} times in {path} ({where})",
            "Use a fuller heading path to disambiguate.",
        )


class NoSuchHeading(stwError):
    def __init__(self, path: str, heading: str) -> None:
        super().__init__(
            "E_NO_SUCH_HEADING",
            f"{path} has no section '{heading}'",
            "Run `stw outline` to list them.",
        )


class StaleSection(stwError):
    def __init__(self, path: str, heading: str, expected: str, found: str, lines: str) -> None:
        super().__init__(
            "E_STALE_SECTION",
            f"{path}#{heading} changed (expected {expected[:8]}…, found {found[:8]}…) at {lines}",
            "Re-read the section and retry.",
        )


class Backlinks(stwError):
    def __init__(self, path: str, srcs: list[str]) -> None:
        super().__init__(
            "E_BACKLINKS",
            f"{len(srcs)} files link to {path}: {', '.join(srcs[:5])}",
            "Use --force, or run `stw backlinks` first.",
        )


class Exists(stwError):
    def __init__(self, path: str) -> None:
        super().__init__("E_EXISTS", f"{path} already exists", "Use `stw write` to overwrite.")


class DimMismatch(stwError):
    def __init__(self, want: int, got: int) -> None:
        super().__init__(
            "E_DIM_MISMATCH",
            f"config dim={want}, stored dim={got}",
            "Run `stw embed --all`.",
        )


class NoEmbedder(stwError):
    def __init__(self) -> None:
        super().__init__(
            "E_NO_EMBEDDER",
            "no [embed] cmd configured",
            "Use --text-only, or set embed.cmd in .stw/config.toml.",
        )


class EmbedFailed(stwError):
    def __init__(self, detail: str, pending: int) -> None:
        super().__init__(
            "E_EMBED",
            f"{detail}. {pending} chunks still dirty",
            "Fix the sidecar and rerun `stw embed`.",
        )


class Locked(stwError):
    def __init__(self, seconds: float) -> None:
        super().__init__(
            "E_LOCKED",
            f"index busy after {seconds:.1f}s",
            "Retry.",
        )


class Usage(stwError):
    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__("E_USAGE", message, hint)
