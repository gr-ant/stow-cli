# Stow — Design

A CLI workspace manager for agents. The agent stows markdown and DuckDB files into a directory it controls; Stow keeps the structure navigable and the context cheap to load.

**Status:** v0 decisions locked (§14). Revision 2 — resolves the embed-prefix/`mv` contradiction, moves the index off DuckDB, and takes the batch embed off the read path.

---

## 1. Premise

The agent owns a workspace. It decides the folder structure, writes markdown into it, spins up DuckDB files where tabular beats prose, and reorganizes all of it over time. Stow is how it does that in one command instead of five.

**Everything goes through the CLI.** Because writes are mediated, the index is correct by construction — no reconciliation loop, no drift, no stale map.

That makes the design principle simple and unforgiving:

> Any command the agent would rather skip is a bug.

Agents route around friction toward whatever is cheapest. The moment `stw write` costs more thought than a plain file write, the index starts lying.

**The friction that matters is cognitive, not temporal.** An agent does not skip a tool because it took 300ms; it skips it because it did not know the tool existed, or because it hit an error it could not recover from. That ordering drives §13: discoverability and error recovery outrank startup latency, and the port to a compiled binary is a later optimization rather than a v0 constraint.

---

## 2. Layout

```
./
├── .stow/
│   ├── stow.db            # index: SQLite (WAL) — registry, headings, links, chunks, embeddings, FTS
│   ├── objects/           # content-addressed history: ab/cdef… (zlib-compressed blobs)
│   ├── config.toml
│   └── embed.py           # optional embedder sidecar (user-supplied)
├── AGENTS.md              # generated command reference — see §13
├── map.md                 # generated — the agent's orientation file
├── notes/, research/…     # markdown the agent stows
└── data/*.db              # DuckDB files the agent creates as workspace artifacts
```

`.stow/stow.db` is the single source of truth. `map.md` is rendered from it. Deleting `map.md` loses nothing; deleting `.stow/` loses the index and the history but not the work, and `stw sync` rebuilds the index from the files on disk.

### config.toml

```toml
[workspace]
include = ["**/*.md", "**/*.db"]
exclude = ["node_modules/**", ".stow/**", ".git/**", "map.md", "AGENTS.md", "CLAUDE.md"]   # generated files stay out of the index

[map]
regenerate = "on-write"      # on-write | on-demand
depth      = 1

[history]
enabled  = true
keep     = 50                # versions per path

[embed]
cmd          = ["python", ".stow/embed.py"]
model        = "bge-small-en-v1.5"
dim          = 384
batch        = 64
prefix_doc   = "passage: "
prefix_query = "query: "
mode         = "deferred"    # deferred | eager | off
max_inline   = 256           # chunks find() will embed before deferring the rest

[chunk]
max_chars = 1200
min_chars = 200
overlap   = 0.15
```

---

## 3. Write surface

Content on stdin, path as the only positional, every flag optional.

```sh
stw new notes/rag.md --about "retrieval strategies" --tags research,wip
stw write notes/rag.md   < content    # create or overwrite
stw append notes/rag.md  < content
stw set notes/rag.md#Chunking/Overlap < content [--expect-sha SHA]
stw mv notes/rag.md research/rag.md
stw rm notes/rag.md
stw tag notes/rag.md +stale -wip
```

**There is no verb-less form.** `stw PATH < content` would save six characters over `stw write PATH` and buy a permanent parse ambiguity against every subcommand name. `write` is the verb.

Every write creates parent directories, injects frontmatter if absent, parses the heading tree and links, snapshots the prior content into `.stow/objects/`, and updates the index. One confirmation line back:

```
stowed research/rag.md · 1.8k · 6 headings · 4 links (1 unresolved)
```

The unresolved-link count in the confirmation matters — it surfaces a problem at the moment the agent has context to fix it, rather than saving it for a `doctor` run that may never happen.

### Section addressing

`stw set notes/rag.md#Chunking/Overlap` replaces one section. This works because the heading tree was indexed at write time.

It is the difference between rewriting a 4k-token file to change one paragraph and sending 200 tokens. On a workspace the agent revises constantly, this is the single largest token saving in the tool — which is why it ships in phase 1, not phase 2.

Rules:

- Heading paths are `H2/H3` slugs, matched case-insensitively.
- The replacement body substitutes everything between the heading line and the next heading of equal-or-shallower level. The heading line itself is preserved unless the new body opens with its own heading of the same level.
- `--expect-sha` takes the section's `content_sha` as previously reported by `outline` or `read`. On mismatch: `E_STALE_SECTION`, with both hashes and the line range named. Without the flag, `set` is a blind overwrite — which is the correct default (the agent usually just wrote the thing) but a dangerous one for long-running work.

**Duplicate headings do not fail a write.** The original design errored at write time, which is friction on the hot path for a problem that only bites at addressing time — a direct violation of §1. Instead: `stw write` emits `W_DUP_HEADING` in its confirmation line, and `set`/`read --section` against an ambiguous path raises `E_AMBIGUOUS_HEADING` naming both line numbers. Legitimate documents (changelogs, dated logs) keep working because full heading paths disambiguate them.

### History

Every destructive write (`write`, `set`, `append`, `rm`, `mv`) snapshots the prior bytes into `.stow/objects/` keyed by sha256. Stow already hashes every file it touches, so this costs one zlib write and nothing else.

```sh
stw log notes/rag.md            # 7 versions · sha · size · when · command
stw undo notes/rag.md           # restore previous version
stw restore notes/rag.md@a3f9   # restore a specific one
```

Without this, `stw write` is an unrecoverable destructive operation on a workspace whose whole premise is that an agent edits it constantly. `keep = 50` per path bounds the store; `stw gc` prunes.

### Why the agent uses it

Bookkeeping is not a reason. These are:

| Command | What it saves |
|---|---|
| `mv` | Rewrites every inbound `[[link]]`. Plain `mv` silently breaks them all. |
| `rm` | Refuses when backlinks exist unless `--force`, and names them. |
| `new` | Handles frontmatter, so the schema never has to be remembered. |
| `set` | Edits one section without reading or rewriting the file. |
| `write` | Map and index update themselves. No sync step to forget. |
| `undo` | Recovers an overwrite that a plain `>` would have made permanent. |

Each is a task the agent would otherwise do by hand or get wrong. That's what makes the CLI the *cheap* path rather than the *disciplined* path — the only kind of discipline that survives contact with an agent.

---

## 4. Databases as artifacts

The agent creates DuckDB files wherever it wants them. They're workspace files, not tables inside Stow's index.

```sh
stw db new data/experiments.db
stw sql data/experiments.db "CREATE TABLE runs(id INT, cfg TEXT, score DOUBLE)"
stw sql data/experiments.db "SELECT cfg, max(score) FROM runs GROUP BY 1" --limit 20
stw db import data/experiments.db --csv results.csv --as runs
stw db export data/experiments.db --table runs --csv out.csv
```

Registered like markdown files, so they appear in the map with their table list and row counts. `stw map` shows the whole workspace — prose and structured together, which is the point.

Stow's own index is SQLite and is never addressable through `stw sql`. CSV is import/export only: DuckDB reads it natively, and a separate CSV engine buys untyped columns and quoting bugs for nothing.

**`import duckdb` is lazy.** It happens inside the `db`/`sql` command bodies only. This is the entire 200–400ms startup complaint from the original draft, and confining it to the two commands that need it removes it from the ~95% of invocations that don't.

---

## 5. Index storage

**The index is SQLite in WAL mode, not DuckDB.** This is a change from revision 1 and it deletes an entire failure mode.

The index workload is OLTP-shaped: one small transaction per file write, point lookups by path, many short reads interleaved with writes from parallel agent tool calls. DuckDB is a single-writer, single-process columnar engine — which is why revision 1 needed `flock` on `.stow/lock`, a 5s timeout, and an `E_LOCKED` error the agent has to recover from. SQLite in WAL mode gives concurrent readers alongside a writer at the library level, so the lock file, the timeout, and `E_LOCKED`-as-a-normal-outcome all go away.

The rest follows: FTS5 for BM25 (mature, ships in the stdlib module) and vectors as `float32` BLOBs scored in-process (§8).

The cost is shipping two engines. Stow needs DuckDB anyway for `data/*.db` artifacts, so the real trade is *two engines each doing what it is good at* versus *one engine plus a lock file and a recoverable-error path*. The second is worse for a tool whose premise is that friction is a bug.

### Schema

```sql
files(path PK, kind, size, mtime_ns, sha256, title, about,
      tags JSON, frontmatter JSON, indexed_at)

headings(path, heading_path, level, byte_start, byte_end,
         line_start, line_end, content_sha)

links(src_path, src_line, raw, target_path, target_anchor,
      kind, resolved)

chunks(chunk_id PK, path, heading_path, byte_start, byte_end,
       text, raw_sha, embed_sha, dirty)

embeddings(embed_sha PK, model, dim, vec BLOB)      -- float32 little-endian

tables(db_path, table_name, row_count, columns JSON)

versions(sha PK, path, size, command, created_at)   -- history index

chunks_fts                                          -- FTS5 external-content over chunks.text
```

### The two hashes

Revision 1 keyed embeddings on a single `content_sha` and claimed `mv` was free — while §7 step 4 prepended `research/rag.md > Chunking` to the embedded text. The file path was *inside the embedded string*, so either `mv` re-embedded the whole document or the stored vector encoded a path that no longer existed. Both, silently.

The fix is to split the two jobs the hash was doing:

- **`raw_sha`** = sha256 of the chunk body alone. Chunk identity, change detection, history.
- **`embed_sha`** = sha256 of the exact string sent to the model.

And to **drop the file path from the embedded prefix.** The embedded text is:

```
passage: Chunking > Overlap

~15% overlap preserves cross-boundary context without…
```

Heading path only. Document-level context lives in `--about` and the map, where it is one string rather than a copy in every chunk. Consequences, now consistent:

- `mv` — free. Nothing in the embedded text changes.
- Reordering headings — free.
- Renaming a heading — re-embeds that subtree only, which is correct: the text genuinely changed.
- Editing a paragraph — re-embeds that chunk, plus neighbors whose `overlap` window included the edit. The original "only genuinely new text reaches the model" was optimistic by roughly the overlap fraction; that is the honest number.

`embeddings` rows are reference-counted against `chunks.embed_sha`; `stw gc` drops orphans left behind by edits.

---

## 6. Embeddings stay off the write path

`mode = "deferred"` is the default and matters more than it looks.

Writes do structure parsing only — pure text work, sub-millisecond. Changed chunks are flagged `dirty`. Embedding happens in batch on `stw embed`, or opportunistically at the head of `stw find`.

Blocking `stw write` on a local model call adds hundreds of milliseconds to the most common command in the tool. Deferring is not an optimization; it's what keeps the mediated-write premise intact.

### `find` must never stall

Revision 1 made `find` the trigger for the deferred batch, which turned the read command an agent runs mid-task into the heaviest write in the system — potentially hundreds of sidecar calls under a held lock. Three rules fix it:

1. `find` embeds at most `embed.max_inline` dirty chunks (default 256), oldest-dirty first.
2. Each batch commits independently, so a `find` interrupted at any point leaves durable progress and never holds a long write transaction.
3. If dirty chunks remain, `find` answers from what is embedded and appends `W_DIRTY: 412 chunks pending — run 'stw embed'` to stderr. It answers; it just says what it did not see.

Large backfills belong in an explicit `stw embed`, which is allowed to take as long as it takes.

### Embedder contract

Stow never loads a model. It shells out: JSONL in, JSONL out, matched on `id`.

```
in : {"id": "a3f9…", "text": "passage: Chunking > Overlap\n\n…"}
out: {"id": "a3f9…", "vector": [0.013, -0.221, …]}
```

Any local runner works — sentence-transformers, llama.cpp's `llama-embedding`, Ollama, a bare ONNX script. A nonzero exit or a malformed line fails the batch with `E_EMBED`, quoting the sidecar's stderr; dirty flags survive so a retry resumes.

`prefix_doc` / `prefix_query` exist because the good small local models (E5, BGE, GTE) are asymmetric and expect `"passage: "` vs `"query: "`. Omitting them silently costs a lot of recall and is invisible unless you know to look.

Model and dim are stored per row. Changing either requires an explicit `stw embed --all` rather than silently mixing vector spaces.

---

## 7. Chunking

Boundaries follow the heading tree, since that's where the semantic breaks are in agent-written prose:

1. Split at H2/H3 sections
2. Over `max_chars` → split on paragraph breaks, carrying `overlap`
3. Under `min_chars` → merge forward into the next sibling
4. Prepend the **heading path** to the embedded text — not the file path (§5)

Step 4 punches above its weight. A chunk reading *"roughly 15% works best in practice"* is unretrievable on its own; prefixed with `Chunking > Overlap`, it carries its own topic. The file it lives in is what `find` prints next to the score, not what the model needs to have memorized.

---

## 8. Read surface

```sh
stw map [--depth N] [--under PATH]
stw ls [GLOB] [--tag T]
stw outline PATH [--sha]
stw read PATH [--section S] [--lines A:B] [--head N]
stw grep PATTERN [--under P] [-C N] [-l]
stw find QUERY [-k N] [--vector-only] [--text-only] [--under P] [--full]
stw links PATH · stw backlinks PATH · stw doctor · stw log PATH
```

### map.md is the orientation file

Not a nicety — it's what the agent reads first to know what it knows. That means it must be cheap to read whole and must describe *purpose*, not just enumerate paths:

```markdown
<!-- generated by stow · do not edit -->
# Workspace map
42 files · 118 links · 2 broken · updated 2026-08-16T09:12Z

## research/
- **rag.md** — retrieval strategies `#research` → 4 out, 2 back
  Chunking · Evaluation · Open questions
- **eval-log.md** — dated experiment notes `#log`

## data/
- **experiments.db** — runs (1,204) · configs (38)
```

The `--about` string from `stw new` is what makes this useful. A map of filenames tells the agent nothing it couldn't glob; a map of purposes tells it where to look.

`--depth 1` by default. A flat map of 500 files is a 10k-token read on every task, which defeats the entire point. `map.regenerate = "on-demand"` exists for workspaces in git, where rewriting `map.md` on every `write` churns the diff.

### Search is hybrid by default

Revision 1 listed `--hybrid` as opt-in and then argued it should be the default. It is now the default, because exact identifiers, error strings, and function names are what BM25 nails and embeddings miss — and an agent's queries are full of them. `--vector-only` and `--text-only` are the escapes, and `--text-only` is what runs when no embedder is configured.

Fusion is reciprocal rank fusion — `Σ 1/(60 + rank)` — which needs no score normalization between two incomparable scales.

Vector scoring is brute force in-process: load candidate `float32` blobs, one numpy matmul, top-k. Tens of milliseconds under ~50k chunks, with no ANN index to build, invalidate, or corrupt. A pure-Python fallback runs when numpy is absent — slower, but the tool still works. Reach for an ANN index only when a workspace genuinely outgrows brute force.

---

## 9. Links

Parse `[[wiki links]]` (with `#anchor`, `|alias`) and `[text](path.md#anchor)`. Resolve by exact path → unique basename → frontmatter alias → unresolved.

`stw doctor` reports broken links, orphans (zero backlinks), ambiguous wiki targets, duplicate headings, dirty chunks, orphaned embeddings, and dim mismatches.

`backlinks` is the highest-value read command in the tool. *"What else references this"* is the question an agent most needs answered and can least afford to answer by grepping the tree.

---

## 10. Token economy

Every byte of output is context the agent pays for.

- Terse by default. `find` returns score, location, two-line excerpt; `--full` for whole chunks.
- `outline` exists so whole-file reads are rarely necessary.
- `sql` applies a default `LIMIT 100` and says so in a footer rather than dumping 40k rows.
- `--json` on everything, for when the agent is piping rather than reading.

```
$ stw find "chunk overlap tradeoffs" -k 3
0.84  research/rag.md#Chunking/Overlap       L88-96
      ~15% overlap preserves cross-boundary context without…
0.71  notes/eval-log.md#2026-07/Retrieval    L12-20
      Bumped overlap to 25%, recall@5 moved 0.61 → 0.63…
0.68  research/rag.md#Chunking/Sizing        L64-79
```

---

## 11. Errors and concurrency

Machine-parseable, on stderr, naming the fix:

```
E_AMBIGUOUS_HEADING: 'Overlap' appears twice in research/rag.md (L88, L142).
E_STALE_SECTION: research/rag.md#Chunking/Overlap changed (expected a3f9…, found 7c21…). Re-read and retry.
E_BACKLINKS: 3 files link to notes/rag.md. Use --force or run `stw backlinks`.
E_DIM_MISMATCH: config dim=768, stored dim=384. Run `stw embed --all`.
E_EMBED: sidecar exited 1 on batch 3/12. 128 chunks still dirty. stderr: …
E_NO_EMBEDDER: no [embed] cmd configured. Use --text-only or run `stw init --embed`.
```

Agents recover well from errors that state the next action and badly from ones that don't.

**Concurrency.** SQLite WAL means readers never block the writer and the writer never blocks readers. Writers serialize inside SQLite with `busy_timeout = 5000`; a genuine 5s timeout is `E_LOCKED`, but it is now a rare pathology rather than the expected outcome of two parallel tool calls. No lock file. `synchronous = NORMAL`, `foreign_keys = ON`.

---

## 12. Sync is the repair tool

With writes mediated, the index is correct by construction. But the agent will occasionally edit a file with `sed`, or a human will open the folder — and a workspace that corrupts when you touch a file directly is a bad workspace.

`stw sync` stats every included path, compares `(size, mtime_ns)` to the registry, hashes only mismatches, and reparses only genuine changes. On an untouched workspace it reads no file contents and takes milliseconds. `--force` rehashes everything.

Read commands stat the files they touch and emit `W_STALE` if something moved underneath them. They answer anyway — but never silently, because a confidently wrong answer from a stale index is worse than no index.

---

## 13. Discoverability is the adoption problem

A CLI the agent doesn't know exists gets used zero times — and in a design where the write path *is* the product, an unused CLI is a workspace with no index at all. This is the highest-risk item in the project, well above startup latency.

`stw init` writes a short, complete command reference into `AGENTS.md` (creating or appending under a `<!-- stow -->` marker it can later update in place). Same for `CLAUDE.md` if present.

The stronger version, and the intended follow-on: ship the same command surface as an MCP server. `AGENTS.md` is a suggestion the agent may not re-read; a tool list is structurally unskippable. The CLI stays the implementation underneath.

---

## 14. v0 decisions

| Question | Decision | Why |
|---|---|---|
| Index engine | **SQLite (WAL) + FTS5** | Concurrent readers; deletes the lock file and `E_LOCKED`-as-normal |
| Artifact engine | **DuckDB**, lazily imported | Only `db`/`sql` pay the import |
| Vector search | float32 BLOB + numpy matmul; pure-Python fallback | No ANN index to corrupt under 50k chunks |
| Embed prefix | Heading path only | Makes `mv` genuinely free (§5) |
| Hash split | `raw_sha` / `embed_sha` | Identity and cache validity are different questions |
| Search default | Hybrid (RRF) | Agent queries are full of exact identifiers |
| Verb-less write | **Cut** | Six characters vs a permanent parse ambiguity |
| Duplicate headings | Warn on write, error on address | Write path stays frictionless |
| History | Content-addressed `.stow/objects` + `log`/`undo` | Nearly free; `write` is otherwise unrecoverable |
| Language (v0) | **Python 3.12**, stdlib-first | numpy and duckdb both optional at import time |
| Binary name | **`stw`** | GNU Stow is widely installed; no PATH collision |

**Language.** The embedder is a subprocess, so the core is free to be anything. Python reaches v0 fastest and the startup complaint is answered by lazy imports (§4) rather than by a rewrite.

Measured on the v0 implementation: `stw read`, `stw map`, and `stw find --text-only` each cost **~60ms** end to end, and neither `duckdb` nor `numpy` is imported on those paths at all. The feared 200–400ms was entirely `import duckdb` at module scope; confining it to `db`/`sql` retires it. Go or Rust for a single binary and ~10ms startup is a v1 question, and a much less urgent one than it looked.

The same lazy-import discipline makes both heavy dependencies genuinely optional: on a stdlib-only interpreter the full write surface, read surface, graph, and hybrid search all work (vector scoring falls back to pure Python), and `stw sql` degrades to a named `E_NO_DUCKDB` naming the install command.

**Name.** GNU Stow is a symlink farm manager that a lot of people have installed for dotfiles. Shipping a binary called `stow` means someone's `stow -R` does something bewildering. The project is Stow; the binary is `stw`.

---

## 15. Build order

1. **Core + write surface** — `init`, `new`, `write`, `append`, `set`, `read`, `outline`, `log`, `undo`. `set` is the largest token saving in the tool and ships first.
2. **Read surface + graph** — `ls`, `grep`, `map`, `links`, `backlinks`, `doctor`, `sync`, `mv`, `rm`, `tag`.
3. **Artifacts** — `db new`, `sql`, `db import`, `db export`.
4. **Retrieval** — chunking, embedder sidecar, `embed`, `find` (vector).
5. **Hybrid** — FTS5 + RRF, `--vector-only` / `--text-only`.
6. **MCP server** over the same command surface (§13).

Steps 1–3 are a complete, useful tool with no ML in it. Ship that first — it also gives the vector layer a working baseline to be measured against instead of being the only thing you have.
