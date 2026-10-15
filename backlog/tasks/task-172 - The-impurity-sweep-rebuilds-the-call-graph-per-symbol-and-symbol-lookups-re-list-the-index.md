---
id: TASK-172
title: >-
  The impurity sweep rebuilds the call graph per symbol, and symbol lookups
  re-list the index
status: In Progress
assignee:
  - '@claude'
created_date: '2026-10-15 02:27'
updated_date: '2026-10-15 02:49'
labels:
  - performance
  - dsl
  - size-3
dependencies: []
modified_files:
  - src/pypeeker/analysis/purity.py
  - src/pypeeker/dsl/sweeps.py
  - src/pypeeker/storage/index_store.py
  - src/pypeeker/query/engine.py
  - tests/test_index_cache_freshness.py
priority: high
type: bug
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
On a large project, `pypeeker check --strict` spends most of its time in two places, measured by cProfile on bh-02's root gate (about 117 s, `--no-refresh`, so all of it is rule time):

- `analysis.purity.impurities` runs once per function from the `no-impure-functions` impurity sweep (`dsl/sweeps.py`, `_impurity_table`), and each call builds `graph.call_graph(store, engine=engine)` from scratch: 1057 builds at about 0.11 s each, about 60% of the run. The sweep's docstring says the shared engine "is the call-graph cache"; it is not, so the sweep is quadratic.
- `SemanticQueryEngine.find_symbol` -> `all_indexes` -> `IndexStore.list_indexed_files` globs the index directory on every symbol lookup: 2949 times, about 63 s of the profile.

Both caches must never serve a stale graph or file list to anything that holds an engine or a store across a change to the files (`check --fix`, `--fix-until-clean`, the overlay the batch planner simulates through, the refactor planners, `privatize`, `demote`, `batch`).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The impurity sweep builds the call graph once per sweep, not once per function; `impurities(...)` keeps its public contract (a new parameter is optional and defaults to building the graph as before)
- [x] #2 Symbol lookups on one engine do not re-list the index directory while the store is unchanged
- [x] #3 A change made between two uses is seen, not served stale, on every path that holds an engine, a store or a corpus across a change: check --fix, --fix-until-clean rounds, the batch planner's overlay, refactor planners, privatize, demote, batch; each has a test, and the task notes say why each is safe
- [x] #4 Findings of `check --strict` are identical before and after on bh-02's root gate and on pypeeker itself; timings before and after are recorded in the task notes
- [x] #5 Each new test was seen failing before the change; scripts/verify-repo.sh exits 0
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Baseline (main cf4f168, nothing changed): bh-02 root gate `check --strict --no-refresh` = 2m22s, exit 0, empty output; pypeeker self `check --strict --no-refresh` = 17.4s (2 heuristic findings, recorded for the after-diff).
2. Call graph, once per sweep: `analysis.impurities` gets an optional keyword `graph` (default None = build it as today). The impurity sweep (`dsl/sweeps.py::_impurity_table`) gets the graph from `corpus.memo(("sweep", "call-graph"), ...)` and passes it to every `impurities(...)` call. Lifetime = the Corpus = one rule run; fix_run already builds a FRESH Corpus after every change (its docstring calls that load-bearing), and `run.rerun`/`plan_privatize`/`batch_intents` do the same, so the graph cannot outlive a change. The engine is not given a graph cache, so no planner/batch/overlay path can see one.
3. Index listing, once per store state: `IndexStore.list_indexed_files` keeps the sorted listing and drops it in `save()` and `remove()` (the only two writers: indexer, applier `_reindex_files` via `rebind_source`, simulate.rebind); returns a copy. This is the same invalidation discipline as the store's per-file parse cache. It is kept in the store, not the engine, because an engine (planner `self._engine`, `privatize`'s pre-filter, the cli engine) can outlive a save while the store is the single choke point every writer goes through; `OverlayIndexStore.list_indexed_files` stays computed on every call from base listing + its own dicts, so overlay saves/removes/deletes and base changes are always seen. The engine's documented live contract (all_indexes re-reads the store) is unchanged.
4. Risky paths and why each is safe (to be copied into the notes with evidence): check --fix single pass, --fix-until-clean (fresh Corpus per round over one overlay), the batch planner overlay (listing recomputed per call; graph never held by the engine), refactor planners (engine per plan, store invalidates on save), privatize, demote, batch.
5. Tests (new file tests/test_index_cache_freshness.py plus additions to tests/test_purity_call_graph.py and tests/test_query_engine.py): (a) call_graph builds counted over a sweep of a multi-function fixture: 1 not N; (b) disk listings counted across many find_symbol on one engine: 1 not N; (c) per risky path, a change between two uses is seen: engine held across store.save/remove, across TransactionApplier.apply, across overlay write/remove; --fix-until-clean where round 2 depends on round 1 and the residual impurity depends on the edited call edge; batch where intent 2 depends on intent 1 creating a file. Each (a)/(b) seen failing on main; (c) seen failing against a deliberately naive variant of each cache (no invalidation / graph held beyond the corpus).
6. Gate: scripts/verify-repo.sh in the foreground; measure bh-02 root gate and pypeeker self after; diff findings before/after; cProfile for the remaining hot spots.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Design as built (differs from the brief's guess in one place, on evidence):
- Call graph: `impurities(..., graph=None)` (optional; None builds it as before). The impurity sweep takes the graph from `corpus.memo(("sweep", "call-graph"), ...)`, built on the first symbol asked about, and passes it to every call. 1057 builds became 1 (0.138 s in the profile).
- Index listing: kept in `IndexStore`, NOT in the engine. Evidence: with the file list held by the engine for its lifetime (tried as a throwaway variant) 3 of the new tests and 1 existing test fail, because an engine does outlive writes (planners hold `self._engine`, privatize's pre-filter holds one across its loop, cli engines span plan+apply). `IndexStore.list_indexed_files` keeps the sorted tuple and `save()`/`remove()` drop it, the same discipline as the per-file parse cache beside it; it returns a fresh list per call. The engine is unchanged apart from its docstring (still live: it keeps neither cache). `OverlayIndexStore` caches nothing: its listing is recomputed per call from the base's listing and its own dicts, so overlay writes and base changes are always seen.

Why each path is safe (every writer of the index goes through `IndexStore.save`/`remove` on the one store instance the composition root builds: indexer, applier `_reindex_files` via `rebind_source`, simulate.rebind; grep finds no other write to the index directory and no rmtree of it):
- check --fix (single pass): Corpus over the real store -> plan_intent_fixes -> TransactionApplier.apply (store.save/remove drop the listing) -> `run.rerun(store)` builds a FRESH Corpus (CheckRun.rerun documents why), so the graph memo of the first corpus is gone. Test: TestCheckFixSeesItsOwnEdit[single-pass].
- check --fix-until-clean: one OverlayIndexStore for all rounds, a fresh Corpus per round (fix_run docstring: load-bearing), so each round builds its own graph; the real store is untouched until the final flatten+apply, then `run.rerun` is fresh again. Test: TestCheckFixSeesItsOwnEdit[until-clean] (an edit that cuts a call edge; every sweep after it must stop flagging the callers).
- OverlayIndexStore / batch planner (run_batch): nothing cached in the overlay; base listing is dropped by any base save. Tests: engine over an overlay sees overlay save/remove and a later base save; a batch whose second intent only works if the overlay lists the file the first intent created (move, then rename of a name the moved symbol imports).
- refactor planners (rename/move/inline/extract/visibility/docstring/delete/imports): each materializer builds its planner and engine per call over the store it is given; the engine keeps no listing; the store drops its listing on the applier's re-index. Test: engine held across plan+apply of a move that creates a file.
- privatize: `run_privatize` reads with one Corpus, then plans; the pre-filter holds one engine across a loop of find_symbol with no writes in the loop (this is where the listing cache pays). Test: run twice on one store with the apply between.
- demote: planner + applier as above. Test: engine held across a demote sees `_name`.
- batch (`run_intent_batch`, `fix` entries): `_expand_fix_rule` builds a Corpus per fix entry before anything runs; run_batch then simulates on an overlay. Test: a second batch on the same store sees the file the first batch created.
Teeth (throwaway variants, all reverted): listing never invalidated -> 4 new tests fail (+4 existing); graph cached beyond the corpus -> both TestCheckFixSeesItsOwnEdit cases fail; engine-lifetime file list -> 3 new (+1 existing) fail.

Measured (bh-02 root gate, `cd /home/user/bh-02 && PYTHONPATH=/home/user/bh-02 pypeeker check --strict --no-refresh`, pypeeker venv Python 3.15.0b4 reads bh-02's index fine; bh-02 .pypeeker checksums identical before/after, git status clean):
- main cf4f168: 2m22.1s real, exit 0, empty stdout and stderr.
- this branch: 1m20.9s real, exit 0, empty stdout and stderr (diff: identical). About 43% faster on this machine.
- pypeeker itself (`index src`, then `check --strict --no-refresh`): 17.4s -> 14.4s; the same 2 heuristic over-exposed-module-symbol findings (batch.py DropReason, ExecutedIntent) before and after, diffed identical. (Its gate does not enable no-impure-functions, so only the listing cache applies.)
cProfile of the branch on bh-02 (160.7s profiled): call_graph 1 call, 0.14s; list_indexed_files 1892 calls but 1 directory walk (0.02s); relative_to 202 calls (was ~600k, 50s: it was mostly the listing walk, so it went with the listing cache); resolve_reference 324k calls (was 19.5M).
Remaining hot spots, not changed: (1) impurities still 39.6s of the profile (25%): `AnalysisContext.for_function` -> `SemanticQueryEngine.find_symbol` called 1889 times, each a linear scan of every symbol (36.8M matcher calls, 29.8s + 8.5s own); an id-keyed lookup would remove it. (2) DSL row evaluation: `expr.evaluate` 3.67M calls (120s cumulative, includes the impurity fact), `dataclasses.replace` 2.6M calls (18.8s) from `_row_context`/`_apply_where`. (3) index loading, 202 files from_json 8s, one time.
Observed, unrelated: `plan_intent_fixes` with a MoveSymbolIntent applies the edits but creates no file (files_created stays empty), so a file-creating intent submitted there would delete the definition without creating its destination; no builtin rule attaches a move remedy, so it is unreachable today.

Plan deviation: all new tests live in tests/test_index_cache_freshness.py; none were added to tests/test_purity_call_graph.py or tests/test_query_engine.py.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Two caches take `pypeeker check --strict` on bh-02's root gate from 2m22s to 1m21s (same empty output, exit 0), and neither can serve a stale answer.

Changes:
- `analysis/purity.py`: `impurities(..., graph=None)`; an optional call graph, default builds it as before.
- `dsl/sweeps.py`: the impurity sweep builds the call graph once, on the first symbol asked about, in the corpus memo (`("sweep", "call-graph")`), and hands it to every `impurities` call. Lifetime = the corpus, which every mutating path replaces after a change.
- `storage/index_store.py`: `IndexStore.list_indexed_files` keeps the sorted listing until the next `save()`/`remove()` and returns a copy. Kept in the store rather than the engine because engines outlive writes (shown by a variant that fails 3+1 tests).
- `query/engine.py`: docstring only.
- `tests/test_index_cache_freshness.py` (15 tests): counts graph builds and directory walks (seen failing on main: 6 builds vs 1, 25 walks vs 1, no `graph` parameter), and one freshness test per path that holds an engine, store, overlay or corpus across a change (check --fix single pass and --fix-until-clean, overlay, batch, second batch, planners/applier, demote, privatize).

Tests: scripts/verify-repo.sh exit 0 (3860 passed, ruff clean, self-lint clean); pypeeker self `check --strict` output identical before and after.
Follow-ups (not done): `find_symbol` is a linear scan called ~1900 times by the sweep (25% of the remaining profile); DSL row evaluation dominates the rest.
<!-- SECTION:FINAL_SUMMARY:END -->
