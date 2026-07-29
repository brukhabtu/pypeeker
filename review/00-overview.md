# pypeeker refactor review — overview

Groundwork for a series of cleanup refactor runs. This directory records what we
know about the repo today and scopes deeper, per-topic investigations. Every
finding here was **verified against the source**, not taken from the design docs
(`architecture.md` / `storage-transaction-architecture.md`), which are known to
lag the code in places.

Status date: 2026-07-29. Verified at suite state `1383 passed, 10 skipped`,
95% coverage, `pypeeker index src && pypeeker check` clean at exit 0 (incl. `--strict`).

## TL;DR

Healthier than a typical "clean this up" codebase. The tool lints itself and
passes; the enforced module layering matches the config exactly (zero
violations, zero unused allowances); tests are 95% covered and run in ~5s; there
is **no** TODO/FIXME/NotImplementedError/stub `pass` anywhere in `src/`. The real
cleanup signal is small and specific. The biggest *refactor hazard* is not the
code but the **tests**: they import private helpers and hard-code symbol-ID and
CLI-JSON string formats, so behavior-preserving changes can still break them.

## Architecture (verified)

87 files, ~19.7k LOC. Strict bottom-up DAG, enforced by the tool's own
`import-boundaries` rule (actual imports == declared allow-list, exactly):

```
models · paths · project        (leaves)
  ├─ adapters → models
  ├─ resolve  → models
  ├─ storage  → models
  │    └─ treebuild → models, storage, paths
  │         └─ query → models, storage, treebuild, resolve
  │              └─ analysis → models, storage, query, resolve
  ├─ binder → adapters, models, paths
  ├─ indexer → adapters, binder, paths, project, storage
  ├─ check  → models, project, storage, resolve, treebuild, analysis, query
  ├─ refactor → adapters, analysis, binder, models, paths, project, query, storage
  └─ app → check, models, refactor, storage   ← only layer allowed to compose check + refactor
       └─ cli.py  (unconstrained composition root; builds every store once, injects down)
```

Three conceptual layers: **language adapter** (`adapters/` + `binder/` +
`refactor/cst.py`), **semantic model** (`models/`, `FileIndex` is the real
language-agnostic contract, everything tagged with `Confidence`), **consumers**
(`query/`, `check/`, `refactor/`). Pipeline: `PythonAdapter.parse` →
`binder.bind` → `FileIndex` → `IndexStore.save` → `treebuild` (`TreeIndex`) →
`SemanticQueryEngine` + on-demand `CrossModuleResolver`. `check` is a linter that
runs *over* the model, not a pipeline stage.

## How it's consumed

**CLI-only** — no MCP server, no SDK, no network API. 21 Click commands in
`cli.py`, JSON on stdout (except `check`, which emits ruff-style text). Two
entry-point names, both real: `pypeeker` and legacy `semantic-tool`. Canonical
client flow (and self-dogfood, and reference CI integration):
`pypeeker index src && pypeeker check`. A second, internal public surface is the
per-package `__init__` barrels (`__all__`), enforced by the `barrel-only` rule
and consumed as a de-facto library API by the tests. On-disk contract:
`.semantic-tool/index/*.json`, `tree.json`, `transactions/<id>.jsonl`,
`check-baseline.json`; symbol-ID grammar `module.path:Scope.Chain:local$N` owned
solely by `models/symbol_id.py`.

## Testing practices

Harness (`conftest.py`, 8 fixtures) composes from `adapter`: `bind_source(src)`
for fast disk-free unit tests, `indexed_project({name: src})` for indexed
projects, `analysis_context` for purity/call-graph. ~one test file per source
module. CLI tested via Click `CliRunner` + `json.loads(result.output)`, never
subprocess. E2E tier: CLI tests + `test_purity_self.py` (runs against pypeeker's
own indexed source; **skips silently unless pre-indexed**). 95% coverage; thin
spots: `models/serialize.py` (80%), `analysis/hierarchy.py` (84%, riskiest to
refactor), `adapters/base.py` (0%, abstract).

## Cleanup targets (ranked)

| # | Target | Safety | Value | Deep dive |
|---|--------|--------|-------|-----------|
| 1 | Dead `_Capability` enum + its only (test) consumer | Very safe | High | 01 |
| 2 | Duplicated `SEMANTIC_TOOL_DIR` constant (defined twice) | Very safe | High | 01 |
| 3 | Stale `architecture.md` command list (+ 6 missing) | Very safe | Med | 05 |
| 4 | Alias-preserving rename: doc says TODO, `--keep-export` exists | Safe | Med | 05 |
| 5 | Doc drift from privatization (`Capability`/`LanguageAdapter`) | Safe | Med | 05 |
| 6 | Inconsistent CLI JSON error envelopes (~15 hand-rolled sites) | Moderate | Med | 02 |
| 7 | 25 heuristic-suppressed "unused public" symbols | Moderate | Med | 01 |
| 8 | `semantic-tool` identity (binary alias + on-disk dir + ~30 refs) | Higher | Med | 04 |

## Status — cleanup run 1 (landed)

Executed via the `pypeeker-cleanup` workflow (4 implement + 4 verify + 1 memo
agents, adversarially verified, gated on full suite + ruff + self-lint —
1395 passed, exit 0):

- ✅ **Landed:** `SEMANTIC_TOOL_DIR` consolidation (target 2); the two CLI bugs —
  `scope` exit code + `privatize --apply` payload (part of target 6); additive
  test hygiene (autouse cwd guard + `sym()` builder); `_Capability` /
  `_LanguageAdapter` removal (target 1, after a sign-off — see below); docs
  reconciliation (targets 3, 4, 5).
- 🟡 **Deferred to `06-open-decisions.md`** (need a product/contract decision):
  `convention_renames.py` delete-vs-wire (memo §1); the `.semantic-tool/` rename
  + binary alias (target 8, memo §2); the broader CLI error-envelope redesign +
  frozen-contract declaration (rest of target 6, memo §3); the 22-symbol
  privatize batch (target 7, memo §4).
- ⚠️ **Note:** target 1 was *not* trivial dead code — the adversarial verify
  caught that `_Capability`/`_LanguageAdapter` were documented roadmap
  scaffolding, so it went through a sign-off (memo §5, resolved: delete) rather
  than auto-landing.

## Deeper investigations (this directory)

Each is an independent, per-topic scoping doc written by a dedicated agent.

- `01-dead-code-privatization.md` — dead code + the 25 heuristic-suppressed
  symbols, traced to real vs test-only consumers, with a per-symbol
  remove/privatize/keep verdict and the safe removal order.
- `02-cli-output-contract.md` — full inventory of every command's JSON success
  and error shape; a proposed standardized envelope; every call site and the
  test blast radius.
- `03-test-coupling.md` — every test that imports private helpers or hard-codes
  symbol-ID / JSON strings; the blast radius per "frozen contract"; which tests
  should move to public-barrel testing.
- `04-naming-identity-migration.md` — every `semantic-tool` leak; the on-disk
  migration story; back-compat decision for the binary alias and `.semantic-tool/`.
- `05-docs-drift.md` — complete `architecture.md` / storage-doc ↔ code drift
  list with proposed edits, including the alias-rename contradiction.

## Consolidated findings from the deep-dives

All five deep-dives are complete. New/sharpened findings beyond the initial scan:

- **01 — dead code:** `_LanguageAdapter` (`adapters/base.py:31`) is also truly
  dead (zero referents) — the only other genuinely-dead symbol besides
  `_Capability`. The tool's own `privatize` auto-fixes **nothing** without flags
  (all 25 nominations skipped `heuristic-confidence`); `--include-heuristic`
  stages 22 renames. Headline: `refactor/convention_renames.py` (~280 lines) has
  **zero `src/` importers** — reachable only from tests, i.e. an unwired feature
  that needs a product decision (keep+wire, or delete with its tests).
- **02 — CLI contract:** found real **bugs**, not just inconsistency — `scope`
  emits an error payload with **exit code 0**; `privatize --apply` grafts an
  `"error"` key onto a success dict; the machine error key is `"code"` in
  `demote`/`promote` but `"reason"` in `purity`. 8 distinct error envelopes
  across ~18 sites. `check`'s text output is intentional; `check --fix` already
  emits JSON.
- **03 — test coupling:** **~1039 hand-typed symbol-ID literals across 54 of 72
  test files** (heaviest: `test_intents.py` 180). 14 tests import private
  helpers. **10 test files `os.chdir` without restore** (ordering is
  load-bearing). Cheap high-value fixes: autouse cwd-guard fixture, a symbol-ID
  builder helper, and declaring the symbol-ID grammar + CLI JSON envelope FROZEN.
- **04 — naming identity:** the whole `.semantic-tool/` dir is **gitignored**, so
  the "invalidates existing indexes" risk is narrower than the doc implies (only
  transactions + baseline are real local state). Nothing depends on the
  `semantic-tool` binary alias. Recommends renaming to `.pypeeker/` with a
  `.semantic-tool/` read-fallback, gated on first consolidating the duplicated
  constant.
- **05 — docs drift:** the alias-rename contradiction is **stale docs, correct
  code** (`--keep-export` is fully implemented). Command list undercounts 11 vs
  21 (incl. `plan-batch`); `move`/`change signature` listed as key ops but
  unimplemented. Consolidated edit list: 6 `architecture.md`, 4 storage-doc,
  2 `CLAUDE.md`.

## Suggested run sequencing (low-risk first)

1. Dead-code + DRY (targets 1, 2) — pure win, no behavior change.
2. Docs-only (3, 4, 5) — no code risk.
3. CLI error-envelope standardization (6) — behavior-visible; freeze the
   envelope first, expect `CliRunner` test churn.
4. Manual privatize batch (7) — per-symbol, dogfood `privatize --include-heuristic`.
5. `semantic-tool` → `pypeeker` identity (8) — own run, needs migration decision.

Two gating decisions up front: (a) are symbol-ID encoding and CLI JSON schema
frozen? (b) should tests stop importing private helpers (test through public
barrels)? See `03-test-coupling.md`.
