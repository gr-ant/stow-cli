# Stow

A CLI workspace manager for agents. The agent stows markdown and DuckDB files
into a directory it controls; Stow keeps the structure navigable and the context
cheap to load.

The design and the reasoning behind every decision are in [plan.md](plan.md).
[CONTRACT.md](CONTRACT.md) is the internal module API.

## Quick start

```sh
./stw init                                     # creates .stow/ and AGENTS.md
./stw new notes/rag.md --about "retrieval strategies" --tags research
./stw write notes/rag.md < draft.md
./stw set notes/rag.md#Chunking/Overlap < para.md   # edit ONE section
./stw map                                      # what's here, and why
./stw find "chunk overlap tradeoffs" -k 5
```

The binary is `stw`, not `stow` — GNU Stow is a symlink farm manager that a lot
of machines already have on PATH (plan.md §14).

## Install

Python 3.11+. No required dependencies.

```sh
pip install -e .            # core
pip install -e '.[dev]'     # + numpy (vector search), duckdb (artifacts), pytest
```

`numpy` and `duckdb` are optional and lazily imported: `stw read` never pays for
either. Without numpy, vector search falls back to pure Python. Without duckdb,
everything except `stw db` / `stw sql` works.

## Tests

```sh
.venv/bin/python -m pytest tests -q
```
