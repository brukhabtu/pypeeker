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
