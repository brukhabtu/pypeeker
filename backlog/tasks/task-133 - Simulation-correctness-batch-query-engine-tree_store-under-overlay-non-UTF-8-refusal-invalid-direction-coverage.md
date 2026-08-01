---
id: TASK-133
title: >-
  Simulation correctness batch: query-engine tree_store under overlay, non-UTF-8
  refusal, invalid-direction coverage
status: Done
assignee: []
created_date: '2026-08-01 16:18'
updated_date: '2026-08-01 17:31'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Three small correctness items from Plan A reviews: (1) planners construct SemanticQueryEngine(index_store) with no tree_store — audit whether cross-file queries during overlay simulation consult stale real-tree state and either route the tree store through the overlay or document why it is safe; (2) extract-method on non-UTF-8 bytes raises an uncaught UnicodeDecodeError (pre-existing) — refuse via a named precondition instead; (3) the invalid-direction MaterializeError code is the one refusal class absent from the reachability matrix — add it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 All three items resolved with tests; gate green; no pre-existing test modified.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Execute via task-pipeline v2 (Opus conductor decides internals; I orchestrate outside). Item 1 has a probe-verified scout plan: the tree_store default is a confirmed unauthorized write-through — SemanticQueryEngine defaults to TreeStore(store.project_root), and under an overlay that IS the real root, so get_tree() on a simulation persists a fabricated tree.json into the real .pypeeker/ (probe-proven). Fix design: store.default_tree_store() polymorphism — IndexStore returns the disk-backed store byte-identically (a pre-existing test pins that), OverlayIndexStore returns a cached in-memory non-persisting one. Items 2 (extract-method non-UTF-8 refusal via named precondition) and 3 (invalid-direction reachability test) scouted fresh in the same run.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Executed as the first conductor-managed task-pipeline run. Conductor decisions: sonnet implementer split in 2 stages, 4 task-specific lenses, frozen policy, re-review armed, plan review SKIPPED with reasoned rationale (scout had validated end-to-end in a scratch copy and probe-disproved the prior scout import design — F821 + no-import-cycles both fail it; the fix flips which module defers). Landed: store.default_tree_store() polymorphism with barrel-exported InMemoryTreeStore (overlay simulations can never persist tree.json — the probe scenario is now a test); SourceIsUtf8 named precondition at the decode site (the enumerated-set test is pinned, so the guard lives in plan(); extract-method on non-UTF-8 now refuses via the standard plan-refused envelope instead of an uncaught UnicodeDecodeError with no JSON); invalid-direction added to the reachability matrix via the schedule-failed library-only template. Review: 7 findings, 0 must-fix (advisories: doc-comment accuracy, load()-by-identity sharing note, a pre-existing batch-flatten decode residual recorded out-of-scope). Gate 1898 pytest (+12), ruff clean, self-lint exit 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Simulation correctness batch (TASK-133), via the conductor pipeline.

- Overlay tree-store write-through FIXED: SemanticQueryEngine now asks the store for its default (store.default_tree_store()); IndexStore returns the disk-backed store byte-identically (pinned test preserved); OverlayIndexStore returns a cached in-memory, non-persisting one — a simulation calling get_tree() can no longer write a fabricated tree.json into the real .pypeeker/ (the original probe scenario is now a regression test).
- extract-method on non-UTF-8 bytes now refuses by name (SourceIsUtf8, slug=None, standard plan-refused envelope) instead of crashing with an uncaught UnicodeDecodeError and no JSON — the only mutating command that lacked the envelope.
- invalid-direction joined the refusal reachability matrix (library-reachable, schedule-failed template).

7 review findings, 0 must-fix. 1898 tests (+12), ruff clean, self-lint exit 0; zero pre-existing test modifications.
<!-- SECTION:FINAL_SUMMARY:END -->
