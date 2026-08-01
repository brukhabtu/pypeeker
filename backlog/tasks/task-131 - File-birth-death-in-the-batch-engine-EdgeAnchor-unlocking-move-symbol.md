---
id: TASK-131
title: 'File birth/death in the batch engine + EdgeAnchor, unlocking move-symbol'
status: In Progress
assignee: []
created_date: '2026-07-31 22:25'
updated_date: '2026-08-01 02:37'
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

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
PR1 landed via its workflow: FileCreateEntry/FileDeleteEntry + EditOp members, TransactionHeader.version (v2 written only when creates/deletes present — edit-only transactions stay byte-identical v1), TransactionLoadError with stable codes replacing raw TypeErrors, LoadedTransaction as a frozen dataclass with a legacy tuple-compat shim (__iter__/__getitem__ yield the old 3-tuple; creates/deletes attribute-only) so all 1608 pre-existing tests pass unmodified — a disclosed deviation from the plan test-migration caveat, chosen because it satisfies both documents with zero suite risk. Applier: stage/commit/restore extended to creates/deletes; _reindex_files now removes index entries for vanished paths generically (closes the ghost-entry hole both directions).

Review: 22 findings, 8 must-fix — headline: cross-entry path collisions (edit+delete same path, create+rename-target, delete+rename-target, duplicate creates) now refused by a new pre-flight _verify_no_path_conflicts, with the edit+rename-source pair exempted as the legal module-rename shape. Final gate 1648 pytest (+40 new: 15 initial + fix-round additions), ruff clean, self-lint exit 0. Scope verified: zero producer-side changes (grep clean on batch/intents). PR2 awaits Plan A PR2 (overlay substrate); PR3 awaits PR2 + Plan A PR1.

PR2 landed via its workflow (opus implementer, rebased onto the overlay substrate per the sequencing adjustment): Effect gains files_created/files_deleted with a full then() composition table (create->delete cancels everywhere incl. files_written; delete->create nets to a plain write; create->rename carries the creation with no rename entry) and identity laws; Materialized gains files_created/files_deleted channels with load_transaction round-trip so PR3 planners need no wiring; _apply_to_overlay is strictly two-phase with a machine-readable refusal hierarchy (file-missing / file-already-exists); schedule gains rule 1b (reader-before-deleter, creator-before-toucher) keeping born-mid-batch batches input-order independent, with same-path creates dropping the later-submitted deterministically; flatten_store emits authorized FileCreateEntry/FileDeleteEntry derived from the folded PREDICTED effect (under-declaring planners still refuse — authorization cannot be laundered through the overlay record).

Review: 19 findings, 3 must-fix — headline: a then() composition hole that could authorize one path as simultaneously created AND deleted. Exactly the two sanctioned tests changed (each keeping its unauthorized half and gaining an applied-for-real round-trip). Final gate 1774 pytest (+30), ruff clean, self-lint exit 0. Zero planner-side production (grep clean) — PR3 scope preserved.
<!-- SECTION:NOTES:END -->
