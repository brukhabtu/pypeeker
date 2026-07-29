# 04 — Naming identity migration: `semantic-tool` → `pypeeker`

Scoping doc for the leftover `semantic-tool` working-title identity. The product,
package, and canonical CLI binary are all `pypeeker`, but the old name still
survives in three distinct identities: a **binary alias**, an **on-disk
directory**, and a long tail of **docstrings / help text / test hardcodes**.

This doc is a decision aid, not a fix. The on-disk directory name is a
**recorded, deliberate decision** (see the naming note in
`storage-transaction-architecture.md:15-24`) — renaming it invalidates existing
indexes/transactions and needs a migration story. The job here is to scope that
migration and lay out options so a human can pick one and hand it to a refactor
run.

Status date: 2026-07-29. All `file:line` references verified against the source
by case-insensitive grep of `semantic.?tool` across `src/`, `tests/`,
`pyproject.toml`, `*.md`, `.github/`, `Dockerfile`, `.gitignore`.

---

## 1. Complete occurrence inventory

Grep pattern: `semantic.?tool` (case-insensitive) — catches `semantic-tool`,
`.semantic-tool`, `semantic_tool`, and `SEMANTIC_TOOL`. There are **no**
`semantic_tool` (underscore module/import) occurrences; the constant is always
`SEMANTIC_TOOL_DIR` and the path is always `.semantic-tool`.

Category legend:
(i) on-disk path/dir identity · (ii) binary console-script alias ·
(iii) code constant/marker · (iv) docstring/comment/help-text ·
(v) test hardcode · (vi) doc file.

### 1a. Production source (`src/`) — the load-bearing set

| File:line | Text (abridged) | Category |
|---|---|---|
| `storage/index_store.py:15` | `SEMANTIC_TOOL_DIR = ".semantic-tool"` | **(iii) constant DEF #1** |
| `storage/index_store.py:24` | `project_root / SEMANTIC_TOOL_DIR / INDEX_DIR` | (iii) usage |
| `storage/index_store.py:3` | module docstring `.semantic-tool/index/*.json` | (iv) |
| `storage/index_store.py:20` | class docstring `.semantic-tool/index/` | (iv) |
| `storage/index_store.py:43` | method docstring `-> .semantic-tool/index/...` | (iv) |
| `storage/transaction_store.py:24` | `SEMANTIC_TOOL_DIR = ".semantic-tool"` | **(iii) constant DEF #2 (DUP)** |
| `storage/transaction_store.py:34` | `project_root / SEMANTIC_TOOL_DIR / TRANSACTIONS_DIR` | (iii) usage |
| `storage/transaction_store.py:3` | module docstring `.semantic-tool/transactions/*.jsonl` | (iv) |
| `storage/transaction_store.py:31` | class docstring | (iv) |
| `storage/transaction_store.py:38` | property docstring | (iv) |
| `storage/tree_store.py:13` | `from ...index_store import SEMANTIC_TOOL_DIR` | (iii) import |
| `storage/tree_store.py:22` | `project_root / SEMANTIC_TOOL_DIR / TREE_FILE` | (iii) usage |
| `storage/tree_store.py:3` | module docstring `.semantic-tool/tree.json` | (iv) |
| `storage/tree_store.py:19` | class docstring | (iv) |
| `storage/tree_store.py:25` | method docstring `creating .semantic-tool/` | (iv) |
| `storage/overlay.py:36` | `from ...index_store import ... SEMANTIC_TOOL_DIR ...` | (iii) import |
| `storage/overlay.py:127` | `/ SEMANTIC_TOOL_DIR` | (iii) usage |
| `refactor/batch.py:88` | `from ...transaction_store import SEMANTIC_TOOL_DIR` | (iii) import |
| `refactor/batch.py:875` | `SEMANTIC_TOOL_DIR not in path.relative_to(...).parts` | (iii) usage |
| `indexer.py:19` | `PROJECT_MARKERS = (".semantic-tool", "pyproject.toml", ".git")` | **(iii) project marker (literal, not the constant)** |
| `check/baseline.py:67` | `BASELINE_RELPATH = Path(".semantic-tool") / "check-baseline.json"` | **(iii) constant (literal, not the constant)** |
| `check/baseline.py:34` | module docstring `.semantic-tool/check-baseline.json` | (iv) |
| `check/builtin/born_private.py:14` | docstring `.semantic-tool/check-baseline.json` | (iv) |
| `models/index.py:12` | docstring `stored in .semantic-tool/index/` | (iv) |
| `cli.py:162` | `--baseline` help: `(.semantic-tool/check-baseline.json)` | **(iv) user-facing help text** |

**Source-of-truth problem (see §4):** the directory name literal
`".semantic-tool"` appears **four independent times** in production code:
`SEMANTIC_TOOL_DIR` in `index_store.py:15`, a **duplicate** `SEMANTIC_TOOL_DIR`
in `transaction_store.py:24`, and two places that hardcode the literal instead
of the constant — `indexer.py:19` (`PROJECT_MARKERS`) and `check/baseline.py:67`
(`BASELINE_RELPATH`). A rename must first collapse these to one source.

### 1b. Packaging / infra

| File:line | Text | Category |
|---|---|---|
| `pyproject.toml:14` | `semantic-tool = "pypeeker.cli:main"` | **(ii) binary alias** |
| `.gitignore:29` | `.semantic-tool/index/` | (i) on-disk path |
| `.gitignore:30` | `.semantic-tool/tree.json` | (i) on-disk path |
| `.gitignore:31` | `.semantic-tool/` | (i) on-disk path (broad — makes 29-30 redundant) |
| `Dockerfile:32` | comment: `this is what .semantic-tool/index/ would contain` | (iv) comment |

`.github/ci.yml.example` invokes only `pypeeker index src` / `pypeeker check` —
**no** `semantic-tool` reference. `architecture.md` is **clean** (its CLI
examples were converted to `pypeeker` in TASK-70).

### 1c. Tests — hardcoded `.semantic-tool/...` paths (v)

18 files, ~35 occurrences. All construct the on-disk layout literally, so all
break under an on-disk rename (decision B) but are untouched by an alias change
(decision A).

| File:line(s) | What it hardcodes |
|---|---|
| `tests/conftest.py:24,25,92` | fixture creates `.semantic-tool/index/` |
| `tests/test_indexer.py:17,18` | test **named** `test_finds_semantic_tool_dir`; makes `.semantic-tool` marker |
| `tests/test_baseline.py:66,110,221,288` | `.semantic-tool/check-baseline.json` (asserts `baseline_path`) |
| `tests/test_check_fix.py:618,652` | `.semantic-tool/check-baseline.json` |
| `tests/test_confidence.py:256` | `.semantic-tool/check-baseline.json` |
| `tests/test_transaction_storage.py:168` | asserts `store.root == .../.semantic-tool/transactions` |
| `tests/test_query_engine.py:150,151,161` | `.semantic-tool/tree.json` |
| `tests/test_overlay_store.py:106,117,128` | byte-compares `.semantic-tool` tree |
| `tests/test_cli_freshness.py:113,131` | `.semantic-tool/index/...` |
| `tests/test_purity_self.py:21` | `INDEX_DIR = REPO_ROOT / ".semantic-tool" / "index" / "src" / "pypeeker"` |
| `tests/test_rename_cli.py:17` | `.semantic-tool/index` |
| `tests/test_transactions_cli.py:17` | `.semantic-tool/index` |
| `tests/test_extract_method.py:14` | `.semantic-tool/index` |
| `tests/test_extract_variable.py:13` | `.semantic-tool/index` |
| `tests/test_convention_renames.py:45` | `.semantic-tool/index` |
| `tests/test_promote_demote.py:38` | `.semantic-tool/index` |
| `tests/test_batch.py:143` | `.semantic-tool/index` |
| `tests/test_purity_cli.py:17` | `.semantic-tool/index` |

Note two tests carry the name in more than a path string:
`test_indexer.py:17` (**test function name** `test_finds_semantic_tool_dir`) and
`test_transaction_storage.py:168` (asserts the `.root` property value).

### 1d. Docs & design records (vi)

| File:line(s) | Note |
|---|---|
| `CLAUDE.md:11,38,99` | states persistence dir is `.semantic-tool/`; line 99 records it as deliberate |
| `storage-transaction-architecture.md:6` | dir-layout diagram root `.semantic-tool/` |
| `storage-transaction-architecture.md:15-24` | the **recorded naming note** (open decision) |
| `review/00-overview.md:55,60,80,86,101,102` | this review series' own cross-refs |

### 1e. Out of scope — historical task records

`backlog/tasks/*.md` contain many `.semantic-tool` mentions (task-8, 21, 26, 28,
70, 75, 86, 89, 97, 98, 99). These are **immutable historical records** written
through the Backlog CLI; they document past work (e.g. transaction hash
`23cb4b83daec` in `.semantic-tool/transactions/` as a rollback anchor). They are
**not** identity leaks to migrate and must not be hand-edited. Listed here only
so the inventory is provably complete.

---

## 2. Two independent decisions

The leaks split cleanly into two decisions that do **not** have to move together.
Conflating them is the main risk; keep them separate on the refactor board.

### Decision A — the `semantic-tool` BINARY ALIAS

- **What:** `pyproject.toml:14` `semantic-tool = "pypeeker.cli:main"`. A second
  console-script entry point that points at the exact same `main`.
- **On-disk impact:** none. It changes only what command name a user can type;
  the directory the tool reads/writes is unaffected.
- **Who depends on it (verified):** *nothing in this repo.* CI
  (`ci.yml.example`), the `Dockerfile` (line 45 `RUN uv run pypeeker index src/`),
  CLAUDE.md, and every test (Click `CliRunner` against `main`, never subprocess)
  invoke `pypeeker`. The only possible consumers are **external**: a user's
  muscle memory or a downstream script that shells out to `semantic-tool`.
- **Cost to keep:** ~zero (one line). **Cost to drop:** one line removed; a
  potential "command not found" for anyone still typing the old name.
- **Verdict:** cheap either way, no coupling to Decision B. Recommend **drop it**
  (single-name product, no internal dependents) *or* keep it one release behind a
  deprecation note — either is a 1-line change. This is a docs-tier change, not a
  migration.

### Decision B — the `.semantic-tool/` ON-DISK DIRECTORY

- **What:** the persisted layout root. Renaming to `.pypeeker/` changes where
  every index file, `tree.json`, transaction JSONL, and `check-baseline.json`
  lives, and the project-root marker the indexer walks up to find.
- **On-disk impact:** high — this is the recorded, deliberate decision. A rename
  makes an existing `.semantic-tool/` invisible: stale indexes, transactions no
  longer discoverable for rollback, and the check baseline silently "reset"
  (every ratcheted violation re-appears as new).
- **Cost:** touches the 4 constant/literal sites in §1a plus ~35 test hardcodes,
  and — depending on option — needs runtime fallback/migration code.

**Key risk-reducer (verified, materially changes the calculus):** the entire
`.semantic-tool/` directory is **gitignored** (`.gitignore:31`). Nothing under it
is committed. Consequences:
- Per-file **indexes** and **`tree.json`** are cheaply regenerable
  (`pypeeker index src`) and the Docker image rebuilds them fresh every build
  (`Dockerfile:45`). "Invalidates existing indexes" means, in practice, *one
  re-index per developer working tree* — seconds of work, no data loss.
- The only artifacts that are **stateful and not trivially regenerable** are
  **transactions** (rollback anchors) and the **check baseline** (the ratchet).
  Both are still gitignored → they are **per-developer local state**, never
  shared through git. So even these are lost only for a local tree that has a
  pending rollback or a locally-seeded baseline at rename time.

The recorded "needs a migration story" caution is real but **narrower than it
reads**: because nothing is committed, the blast radius is a developer's local
working copy, not the fleet. That is what makes Option 2 below cheap.

---

## 3. Decision B — migration options

### Option 1 — Leave as-is; document the mismatch better

- **Code change:** none.
- **Test change:** none.
- **Docs:** tighten the existing note (`storage-transaction-architecture.md:15`)
  and `CLAUDE.md:99` to state clearly *why* (working-title leftover, gitignored,
  regenerable) and that it's a settled non-decision.
- **Back-compat:** perfect — nothing moves.
- **Risk:** zero code risk. Cost is permanent low-grade confusion: a `pypeeker`
  binary writing a `.semantic-tool/` dir, and the DRY problem in §4 stays.

### Option 2 — Rename to `.pypeeker/` with read-old fallback + lazy migrate *(recommended)*

- **Behavior:** the tool writes `.pypeeker/`. On read, if `.pypeeker/` is absent
  but `.semantic-tool/` exists, it reads the old dir (and, on the next `index`,
  writes to `.pypeeker/` — effectively migrating). `PROJECT_MARKERS` accepts
  **both** names so root discovery never regresses.
- **Code scope:**
  1. **First** collapse the DRY problem (§4): one `SEMANTIC_TOOL_DIR` (rename to
     e.g. `STORAGE_DIR`) in `storage/index_store.py:15`; delete the duplicate in
     `transaction_store.py:24` and import it; replace the two hardcoded literals
     at `indexer.py:19` and `check/baseline.py:67` with the constant.
  2. Introduce `STORAGE_DIR = ".pypeeker"` + `LEGACY_STORAGE_DIR = ".semantic-tool"`.
  3. Add a small resolver (in `storage/`, injected via the `cli.py` composition
     root like the stores) that returns `.pypeeker/` for writes and picks the
     existing dir for reads. `IndexStore`, `TreeStore`, `TransactionStore`,
     `overlay.py`, and `refactor/batch.py`'s mirror-exclusion (`batch.py:875`)
     consume it. `indexer.py` `PROJECT_MARKERS` lists both names.
- **Test scope:** the ~35 hardcodes in §1c must move to the new dir; ideally
  route them through a shared fixture/constant so a future rename is one edit
  (see §4). Add coverage for the fallback (old dir present → read works; next
  index writes new dir) and for `PROJECT_MARKERS` accepting both. Rename the test
  function `test_finds_semantic_tool_dir` (`test_indexer.py:17`).
- **Back-compat:** strong. Existing local `.semantic-tool/` trees keep working;
  transactions stay discoverable for rollback; the baseline is not silently reset.
- **Risk:** moderate. Fallback logic is a new branch to get right (precedence
  when *both* dirs exist; whether to leave the old dir or clean it up). But it's
  the only option that preserves in-flight transactions and the ratchet.

### Option 3 — Hard rename with a version bump / "regenerate required" note

- **Behavior:** write and read `.pypeeker/` only. No fallback. Old dir is dead;
  users re-run `pypeeker index`.
- **Code scope:** same DRY collapse as Option 2, then swap the one constant's
  value to `.pypeeker`; **no** fallback resolver. Smaller code delta than Opt 2.
- **Test scope:** same ~35 hardcodes updated; no fallback tests. Slightly less
  than Opt 2.
- **Back-compat:** none by design. Mitigated by: index/tree regenerate in
  seconds; but **pending transactions and a locally-seeded baseline are lost**
  unless the user manually `mv .semantic-tool .pypeeker`. Pair with a bump
  `0.1.0 → 0.2.0` and a CHANGELOG/release note: "storage dir renamed; run
  `pypeeker index`, and `mv .semantic-tool .pypeeker` to keep pending
  transactions/baseline."
- **Risk:** low code risk, higher *user-surprise* risk (silent baseline reset is
  the sharp edge — the ratchet quietly forgets its history).

### Recommendation

**Option 2 (rename + fallback), gated behind first doing the §4 DRY collapse as a
standalone, already-planned safe refactor.** Rationale:

- The gitignore fact makes the *index* migration nearly free, but the
  **transaction rollback anchors** and the **baseline ratchet** are the genuinely
  stateful artifacts; only Option 2 preserves them without asking every developer
  to run a manual `mv`. The fallback branch is small and self-contained.
- Option 1 is the honest fallback if the team decides the churn isn't worth it —
  in which case still do §4 (it's independently valuable) and just improve the
  docs.
- Option 3 is acceptable only if the team is comfortable with a version-bumped
  hard break and a release note; the silent baseline reset is the reason to
  prefer Opt 2's fallback.

Sequence: **§4 DRY collapse first (safe, no behavior change) → then Option 2.**
This matches `00-overview.md`'s ranking (targets 2 then 8) and keeps the rename a
one-constant edit instead of a four-site hunt.

---

## 4. Single-source-of-truth prerequisite (do this first)

The directory name is not defined once. It is spelled `".semantic-tool"` in
**four** independent places in `src/`:

| Site | Form |
|---|---|
| `storage/index_store.py:15` | `SEMANTIC_TOOL_DIR = ".semantic-tool"` (canonical) |
| `storage/transaction_store.py:24` | `SEMANTIC_TOOL_DIR = ".semantic-tool"` (**exact duplicate**) |
| `indexer.py:19` | literal inside `PROJECT_MARKERS` tuple |
| `check/baseline.py:67` | literal inside `BASELINE_RELPATH = Path(".semantic-tool") / ...` |

Consumers are split across the two constant copies: `tree_store.py:13` and
`overlay.py:36` import from `index_store`; `refactor/batch.py:88` imports from
`transaction_store`. So the two definitions can drift independently — a rename
that edits one and misses the other yields a tool that writes to two different
directories.

**This is the same duplication flagged in `01-dead-code-privatization.md`**
(overview target #2, "Duplicated `SEMANTIC_TOOL_DIR` constant (defined twice)",
rated *Very safe / High value*). Cross-reference that doc for the pure-DRY
verdict; this doc adds the two *literal* sites (`indexer.py`, `baseline.py`) that
a rename must also fold in, which a duplication-only pass might not touch.

Fix before any rename: one exported constant (rename to a name-neutral
`STORAGE_DIR` so the identity isn't re-baked in), all four sites reference it.
Then Decision B is a one-line value change (plus the fallback for Option 2).

---

## 5. Doc updates required if Decision B changes

If the directory is renamed (Option 2 or 3), update in the same run:

- **`storage-transaction-architecture.md`**
  - `:6` — dir-layout diagram root `.semantic-tool/` → `.pypeeker/` (and, for
    Opt 2, note the legacy fallback).
  - `:15-24` — the naming note is now stale. Replace "open decision" with the
    resolution: dir is `.pypeeker/`; for Opt 2 record the fallback + lazy-migrate
    behavior and that `.semantic-tool/` is still read if present.
- **`CLAUDE.md`**
  - `:11`, `:38` — "persists under `.semantic-tool/`" → `.pypeeker/`.
  - `:99` — the "On-disk dir is `.semantic-tool/` (not `.pypeeker/`) — deliberate
    leftover" convention line is now **wrong** and must be rewritten (or deleted)
    to describe the new name / migration.
- **`Dockerfile:32`** — comment mentioning `.semantic-tool/index/`.
- **`.gitignore:29-31`** — add `.pypeeker/` (keep `.semantic-tool/` for Opt 2 so
  old trees stay ignored; drop it for Opt 3).
- **`review/00-overview.md`** — `:60` on-disk contract line; `:80/:86` ranking
  rows referencing the constant/identity, if the review series is kept current.

If Decision A changes (drop the alias), update:
- **`pyproject.toml:14`** — remove the `semantic-tool` script line.
- **`review/00-overview.md:55`** — "Two entry-point names, both real" is then
  false; correct to one.
- No source, test, CI, or Dockerfile change (none depend on the alias).

---

## 6. Handoff summary for a refactor run

1. **Prereq (safe, no behavior change):** collapse the four dir-name sites (§4)
   to one neutral `STORAGE_DIR` constant. Ships independently; also closes
   overview target #2.
2. **Decision A (1 line, independent):** drop or deprecate the `semantic-tool`
   console alias in `pyproject.toml:14`. Nothing internal depends on it.
3. **Decision B (recommended Option 2):** `STORAGE_DIR = ".pypeeker"` +
   `.semantic-tool/` read fallback + both-name `PROJECT_MARKERS`; migrate the ~35
   test hardcodes (§1c) through a shared fixture/constant; rename the
   `test_finds_semantic_tool_dir` function; update the docs in §5.
4. Re-run the gate: `uv run pytest -q`, `uv run ruff check src tests`, and the
   self-lint `uv run pypeeker index src && uv run pypeeker check`. Confirm a tree
   with a pre-existing `.semantic-tool/` still resolves (Opt 2) and that the
   baseline ratchet is not reset.
