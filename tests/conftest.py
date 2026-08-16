"""Shared test fixtures. Every test gets a real workspace in a tmp dir."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def ws_dir(tmp_path, monkeypatch):
    """An initialized workspace, cwd'd into. Returns the root Path."""
    monkeypatch.chdir(tmp_path)
    run("init")
    return tmp_path


def run(*args: str, stdin: str | None = None, cwd: Path | None = None):
    """Invoke the CLI in-process-ish (subprocess for isolation). Returns CompletedProcess."""
    return subprocess.run(
        [sys.executable, "-m", "stow", *args],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        env={**_env(), "PYTHONPATH": str(ROOT)},
    )


def _env():
    import os

    return dict(os.environ)


@pytest.fixture
def cli(ws_dir):
    """run() bound to the workspace dir."""
    def _run(*args, stdin=None):
        return run(*args, stdin=stdin, cwd=ws_dir)
    return _run
