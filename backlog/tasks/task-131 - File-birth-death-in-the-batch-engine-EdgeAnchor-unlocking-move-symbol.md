---
id: TASK-131
title: 'File birth/death in the batch engine + EdgeAnchor, unlocking move-symbol'
status: In Progress
assignee: []
created_date: '2026-07-31 22:25'
updated_date: '2026-07-31 22:59'
labels: []
dependencies:
  - TASK-129
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan D in roadmap-plans.md (normative, incl. sequencing adjustments — PR2 rebased onto the overlay substrate; MoveSymbolIntent destination footprint unconditional; import-edge reads bound to the store-routed API). Three PR slices, each its own workflow execution: PR1 transaction model + store + applier (FileCreateEntry/FileDeleteEntry, header versioning, apply/rollback as exact inverses — independent, can run parallel with Plan A); PR2 effect algebra + birth/death in the simulation loop + schedule + flatten (requires Plan A PR2); PR3 EdgeAnchor + MoveSymbolIntent + MoveSymbolPlanner + CLI (requires PR2 and Plan A PR1).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Each PR slice lands independently green; all Plan D acceptance criteria in roadmap-plans.md are met, including apply/rollback as byte-exact inverses over creates/deletes/edits/renames, pre-1.0 transaction files loading unchanged, and move-symbol moving a top-level symbol with all importers rewritten in one rollback-clean transaction.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Execute roadmap-plans.md Plan D slice by slice, each as its own workflow with orchestrator verify + PR + merge between slices. PR1 (now): transaction model + store + applier — FileCreateEntry/FileDeleteEntry, header versioning, LoadedTransaction dataclass conversion, applier stage/commit/restore for creates/deletes, apply/rollback as byte-exact inverses, backward-compat loading; proven via hand-built transactions (no producer yet), no pre-existing test modified. PR2 (after Plan A PR2): effect algebra + birth/death through the overlay engine + schedule + flatten. PR3 (after PR2 + Plan A PR1): EdgeAnchor + MoveSymbolIntent + MoveSymbolPlanner + CLI.
<!-- SECTION:PLAN:END -->
