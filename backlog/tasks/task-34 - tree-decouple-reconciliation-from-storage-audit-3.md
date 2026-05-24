---
id: TASK-34
title: 'tree: decouple reconciliation from storage (audit #3)'
status: Done
assignee:
  - '@claude'
created_date: '2026-05-24 00:22'
updated_date: '2026-05-24 00:22'
labels:
  - index
  - architecture
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Audit follow-up (#3). tree.py's load_or_rebuild mixed the incremental-reconciliation logic with storage I/O (loading all indexes, loading/saving the cached tree), unlike resolve.py and analysis/graph.py which take injected FileIndexes. This extracts the pure logic so the tree's core matches the rest of the codebase's dependency style.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A pure reconcile_tree(indexes, cached) -> RebuildResult holds the fast-path + subtree-reuse logic with no store access or persistence
- [x] #2 load_or_rebuild becomes a thin I/O wrapper: load indexes + cached tree, call reconcile_tree, persist only when changed; behavior is identical to before
- [x] #3 reconcile_tree is unit-tested with injected indexes (first build, no-change fast path returns cached object, edit rebuilds only affected subtree); full suite green; pypeeker check exits 0
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Extract reconcile_tree(indexes, cached) from load_or_rebuild; wrapper loads/saves; persist iff cached is None or rebuilt/removed; add pure reconcile tests.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Split tree.py: reconcile_tree(indexes, cached) is pure (fast-path manifest compare, build_tree, subtree reuse); load_or_rebuild loads indexes from IndexStore + cached from TreeStore, calls reconcile_tree, and saves only when cached is None or rebuilt/removed is non-empty. Behavior identical; existing store-based tests unchanged. Added TestReconcileTree (no stores).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Decoupled the symbol-tree reconciliation from storage (audit #3). The incremental logic — fast-path manifest comparison, bottom-up rebuild, and subtree reuse — now lives in a pure reconcile_tree(indexes, cached) -> RebuildResult that takes injected FileIndexes and performs no I/O, matching the dependency style of resolve.py and analysis/graph.py. load_or_rebuild is now a thin wrapper that loads indexes + the cached tree, calls reconcile_tree, and persists only when something changed; behavior is identical.

Tests: 407 pass, incl. new pure reconcile_tree tests (first build, no-change fast path returns the cached object, edit rebuilds only the affected subtree). pypeeker check exits 0.
<!-- SECTION:FINAL_SUMMARY:END -->
