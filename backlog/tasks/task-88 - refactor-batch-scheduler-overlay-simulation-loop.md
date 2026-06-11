---
id: TASK-88
title: 'refactor: batch scheduler + overlay simulation loop'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 18:27'
updated_date: '2026-06-11 21:01'
labels:
  - refactor
  - m3-planner
dependencies:
  - TASK-86
  - TASK-87
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The core engine: order intents (explicit deps + footprint conflicts; id-changing ops late, deletes after readers, deterministic tie-break), then execute sequentially against the overlay — materialize edits, mutate overlay, incrementally re-bind touched files, remap anchors, re-validate each intent's preconditions at its turn. Stale intents drop with reasons (skip-and-report) or abort (all-or-nothing), per policy. Fixpoint-capped.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Scheduler produces a deterministic order from deps + footprints; cycles and hard conflicts are reported, not silently resolved
- [x] #2 Simulation applies intents on the overlay with guarded re-validation; dropped intents carry machine-readable reasons
- [x] #3 skip-and-report and all-or-nothing policies both work; iteration is bounded; tests cover interfering renames, inline-then-delete-import chains, and a stale-guard drop
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Read substrate: intents/footprint/preconditions, four planners, overlay store, simulate.rebind, applier mechanics (done)
2. Extend refactor/simulate.py with a store-agnostic rebind_source helper (overlay rebind delegates to it) so the batch loop can rebind mirror files
3. NEW refactor/batch.py: pure scheduler (explicit deps + footprint-conflict edges; renames late, deletes after readers, deterministic (file, position, id) tie-break via heap Kahn; cycle -> ScheduleCycleError; same-symbol rename/rename hard conflict -> deterministic later-drop), temp-mirror simulation loop (planners re-plan against an IndexStore rooted at a mirror dir because planners/dataflow read source via project_root from DISK), per-intent guarded re-validation, bottom-to-top edit splice, rebind, Effect folding + pending remap with orphan drops, BatchResult + skip_and_report/all_or_nothing policies (BatchAborted)
4. NEW tests/test_batch.py covering ordering rules, cycle error, hard-conflict determinism + all-or-nothing abort, stale-guard precondition drop, orphan drop, anchor remap through rename, interfering renames, end-to-end rename+inline+fix across files with untouched real project
5. uv run pytest -q + ruff clean; notes + final summary
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Scheduler (refactor/batch.py, pure): explicit deps + one oriented edge per footprint-conflict pair. Rule precedence: deletes after readers of the deleted target (reads_symbols + scoped-fact scopes, prefix-aware), id-changing intents (predicted renamed/files_renamed) after non-id-changing, then deterministic (primary file, anchor position, intent id) tie-break; the same key drives heap-based Kahn, so order is submission-order independent. Cycles -> ScheduleCycleError carrying the cycle ids; duplicate/unknown ids -> ScheduleError.
- Explicit deps outrank conflict policy: pairs already ordered by the dep graph (transitively) get no conflict edge, so a user-declared order contradicting a tie-break cannot manufacture a phantom cycle. Genuine dep cycles and multi-node policy-edge cycles are still reported.
- Hard conflicts: two id-changing intents writing the exact same symbol id (two renames of one symbol) have no resolving order -> the later-submitted one drops with reason conflict-dropped (deterministic); dependents cascade. Prefix-overlapping renames (m:Foo vs m:Foo.method) are NOT hard -- they compose via remap and are ordered by tie-break.
- Substrate decision: temp-dir MIRROR + plain IndexStore, not OverlayIndexStore, because RenamePlanner._build_edits, the CST planners, inline, and dataflow.analyze_range all read source via (project_root / path).read_bytes() -- DISK -- which bypasses the overlay file-bytes layer. materialize_mirror copies indexed files + pyproject.toml into a throwaway dir and re-saves every FileIndex into a fresh IndexStore there; planners then re-plan unmodified and every read/write resolves inside the mirror, real tree byte-for-byte untouched. It reads THROUGH an OverlayIndexStore when handed one (overlay-simulated state feeds the mirror), and the overlay store remains the documented zero-copy future once planners read bytes through the store.
- Guarded re-validation: each intent re-plans through its planner against the mirror at its turn (planners re-run their precondition sets inside plan()); planner errors / fix declines -> drop reason precondition-failed with the planner message as detail. DeleteSymbolIntent is schedulable (ordering + remap) but has no executor in v1 -> precondition-failed drop, documented.
- Loop: materialize edits (planner transactions are loaded back from a mirror-rooted TransactionStore), splice bottom-to-top per file with old-text verification (two-phase: all files computed before any written), apply file renames, rebind touched files via new simulate.rebind_source (store-agnostic core factored out of the overlay rebind), fold predicted Effect via Effect.then, remap all pending intents (orphans -> reason orphaned). Single pass, no fixpoint; per-intent plan budget pinned by MAX_PLAN_ATTEMPTS_PER_INTENT = 1.
- Policies: skip_and_report collects DroppedIntent records; all_or_nothing raises BatchAborted (with the full drop report) on the first drop, schedule-time drops abort before the mirror is even created.
- tests/test_batch.py (28 tests): ordering rules incl. rename-after-body-edit and delete-after-reader with adversarial tie-break ids, dep-overrides-tie-break, 2- and 3-node cycle errors, hard-conflict later-drop determinism + cascade, interfering renames under both policies, stale-guard inline drop (fix deletes the assignment, inline re-plan fails with Symbol not found), duplicate-inline orphan, anchor remap mod:Foo.method -> mod:Bar.method landing as a real edit, inline-then-delete-import chain (fix replans against post-inline bytes, hash-asserted), end-to-end rename+inline+fix across 3 files with hand-computed mirror contents and untouched real project, overlay-fed mirror materialization.
- uv run pytest -q: 1148 passed; ruff clean on batch.py / simulate.py / test_batch.py.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added the M3 batch engine: a pure deterministic scheduler over refactor intents plus a simulation loop that executes them against a temp mirror of the project, with guarded per-intent re-validation, anchor remapping, and skip-and-report / all-or-nothing policies.

Changes:
- NEW src/pypeeker/refactor/batch.py: schedule() orders intents from explicit deps + footprint-conflict edges (deletes after readers of the deleted target; id-changing intents late; deterministic (file, position, intent-id) tie-break driving heap-Kahn, so order is submission-order independent). Cycles raise ScheduleCycleError listing the cycle; two id-changing intents writing the same symbol (two renames of one symbol) are a hard conflict: the later one drops deterministically (conflict-dropped) and dependents cascade, never silently reordered. Explicit deps outrank conflict-edge policy for pairs they already order.
- run_batch() simulates the schedule on a temp-dir mirror (materialize_mirror copies indexed files + pyproject.toml and re-saves the FileIndexes into a fresh IndexStore): each intent re-plans through its real planner against the mirror at its turn (that re-runs the planner precondition sets = the guard), edits splice bottom-to-top per file with old-text verification, touched files re-bind, and the intent's Effect folds into a running substitution through which all pending intents are remapped (orphans drop with reasons). Single pass, bound pinned by MAX_PLAN_ATTEMPTS_PER_INTENT.
- Substrate decision: mirror over OverlayIndexStore because all four planners + dataflow read source bytes from disk via project_root, bypassing an overlay's file layer; the mirror keeps them unmodified and provably cannot touch the real tree. materialize_mirror reads THROUGH an overlay when given one, and the overlay remains the documented zero-copy follow-up.
- Result model: BatchResult(executed ExecutedIntent records with per-intent materialized edits hash-pinned to the intermediate state they were planned against, dropped with machine-readable reasons precondition-failed/orphaned/conflict-dropped, mirror root + fresh store as the state handle for TASK-89, folded total Effect). All-or-nothing aborts via BatchAborted carrying the drop report.
- src/pypeeker/refactor/simulate.py: factored store-agnostic rebind_source() out of the overlay rebind (unchanged behaviour) so the loop can rebind mirror files.

Tests (tests/test_batch.py, 28 tests):
- ordering rules with adversarial tie-break ids, dep-overrides-tie-break, cycle errors (2- and 3-node), hard-conflict drop determinism + cascade
- interfering renames under both policies; stale-guard inline drop (Symbol not found after a prior fix deletes the assignment); duplicate-inline orphan; DeleteSymbolIntent schedulable-but-unexecutable
- anchor remap through a class rename (mod:Foo.method lands as mod:Bar.method); inline-then-delete-import chain with the fix replanning against post-inline bytes (hash-asserted)
- end-to-end rename+inline+fix across 3 files: hand-computed final mirror contents, fresh mirror index, real project byte-for-byte untouched; overlay-fed mirror materialization

uv run pytest -q: 1148 passed. ruff clean.

Risks/follow-ups: DeleteSymbolIntent has no executor yet (drops with a documented reason); zero-copy overlay substrate once planners read bytes through the store; TASK-89 consumes BatchResult.root/store for flattening.
<!-- SECTION:FINAL_SUMMARY:END -->
