---
id: TASK-160
title: 'models/paths: module-id collisions — specify or eliminate'
status: Done
assignee:
  - '@claude'
created_date: '2026-08-08 19:59'
updated_date: '2026-08-08 22:18'
labels:
  - models
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found during TASK-155b: paths.module_path_from is not injective over indexed files (app/dup/mod.py vs app/dup/mod/__init__.py share a module id; multi-root and nested src roots collide too), so symbol ids can collide across files. This silently dropped rows in a dict keyed by symbol id (real bug, caught and fixed by reshaping in dsl/sweeps.py) and makes representative-file keying ambiguous. Resolve deliberately: either make module ids injective (id grammar change — heavy, touches the storage symbol-id contract in storage-transaction-architecture.md) or SPECIFY collision semantics as the documented contract and audit every id-keyed map below dsl for collision safety. Scout must inventory the id-keyed maps and recommend; the cheap documented-semantics path is acceptable if the audit shows defenses everywhere.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Collision semantics are either eliminated or specified in the storage/architecture docs, with every id-keyed consumer below dsl audited against the choice
- [x] #2 Full gate green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v4 in /home/user/pypeeker-wt160, parallel with TASK-159. TASK-161 sequenced after both (shared binder files).
<!-- SECTION:PLAN:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Resolved on the documented-semantics path (PR #127).

- Contract: storage-transaction-architecture.md gains "Module ids are not injective over files" — every id-keyed structure at or below dsl audited and classified (safe / accumulating / deterministic election / fixed), including the sharper finding that ids are not unique within one file either (comprehensions, duplicate defs, overloads; 39/134 files in our own index). architecture.md cross-references.
- Fixed the three collision-unsafe consumers invisible to the frozen engine: query/engine._module_to_indexes (tree/members now report all colliding files), treebuild._elected_manifest (reconcile fast path can hit again), dsl/selection._apply_follow (one row per file for follow("module")).
- Left the frozen-observable elections (resolver last-wins vs locate first-wins, hierarchy, purity) documented but unchanged; unification is post-flip work.

Tests: 15 new collision tests (3,413 total); four-step gate PASS in worktree and after merge.
<!-- SECTION:FINAL_SUMMARY:END -->
