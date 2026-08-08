---
id: TASK-160
title: 'models/paths: module-id collisions — specify or eliminate'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-08-08 19:59'
updated_date: '2026-08-08 20:00'
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
- [ ] #1 Collision semantics are either eliminated or specified in the storage/architecture docs, with every id-keyed consumer below dsl audited against the choice
- [ ] #2 Full gate green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v4 in /home/user/pypeeker-wt160, parallel with TASK-159. TASK-161 sequenced after both (shared binder files).
<!-- SECTION:PLAN:END -->
