---
id: TASK-134
title: >-
  Plumbing hygiene: Materialized carry-out single source, trait lookup perf,
  compat shims inventory
status: Done
assignee: []
created_date: '2026-08-01 16:18'
updated_date: '2026-08-01 18:53'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Debt noted across reviews, none behavior-affecting: (1) submit_intent rebuilds Materialized from ExecutedIntent by a hand-copied field list — three unlinked copies of the shape; give the carry-out one constructor/helper so a new field cannot be silently dropped (the files_created drop in D-PR3 was exactly this failure mode); (2) the type-annotation trait provider re-resolves symbols by linear scan and prefer_tuple pays O(candidates x symbols) — index by symbol_id; (3) inventory the LoadedTransaction/_FlattenedTransaction tuple-compat shims and either schedule destructure-site migration or record the shims as permanent; (4) the trait conformance test identifies builtin providers by module-prefix heuristic — make it explicit.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Carry-out has a single construction path with a test proving a new field cannot be dropped silently; trait lookups indexed; shim decision recorded; gate green.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Executed via conductor task-pipeline in worktree pypeeker-wt134, in parallel with TASK-132 (file-disjoint).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
STAGE C (item 3 — tuple-compat shim retirement):
- Independent function-scoped AST probe found 83 destructure/index sites (10 more than the scout inventory: 7 `tx_store.load(...)[0]` direct subscripts in test_applier.py, 2 indirect destructures in test_transaction_storage.py — a file the inventory missed entirely — and 1 direct subscript in test_submit_one_path.py). ZERO in src/, confirming both shims had no production consumers.
- Migrated all 83 sites to attribute access across 15 test files; retired `_as_triple`/`__iter__`/`__getitem__` from LoadedTransaction and `_as_pair`/`__iter__`/`__getitem__` from _FlattenedTransaction.
- Ported (not deleted) `test_supports_legacy_tuple_unpacking_and_indexing` to `test_exposes_header_edits_rename_and_file_entries_by_attribute`; retargeted the save-guard regression to the attribute form; renamed TestLoadedTransactionCompat -> TestFileEntryDropGuard.
- Added pytest.raises(TypeError) ratchets in both lifecycle test files so restoring either shim fails the suite (mutation-verified: restoring the shims fails exactly those 2 tests and nothing else).
- Decision recorded in architecture.md ("Tuple-compat shims: retired (TASK-134)"), including the _FlattenedTransaction/LoadedTransaction guard asymmetry (flattened headers mint at version 1, so save's file-lifecycle guard could never fire for them) as a named follow-up; stale prose in storage-transaction-architecture.md corrected.

Executed via conductor pipeline in worktree (parallel with TASK-132). Conductor: opus implementer, plan review ON, 3-way split, 4 tailored lenses, re-review armed. Landed: single _CARRIED_FIELDS-driven carry seam (a reflection test proves a new Materialized field cannot be silently dropped); trait providers index symbol lookups (finding-identity lens verified byte-identical findings); shims RETIRED — all 73 tuple-destructure sites across 13 test files migrated to attribute access, and the scout-discovered open hazard (a _FlattenedTransaction 2-element destructure + save() silently dropping creates/deletes past the version guard) is closed with the shim; conformance-test builtin identification made explicit. Review: 6 findings, 0 must-fix. Gate 1913 pytest, ruff clean, self-lint exit 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Plumbing hygiene (TASK-134), via the conductor pipeline in a parallel worktree.

- One construction path for the Materialized/ExecutedIntent carry seam, driven by a shared _CARRIED_FIELDS source of truth; a reflection-based test makes silently dropping a future field impossible (the files_created failure mode can not recur).
- Trait providers look up symbols by id index instead of linear scans; prefer_tuple no longer pays O(candidates x symbols); findings byte-identical.
- Tuple-compat shims retired on evidence: zero src destructure sites remained, all 73 test sites migrated to attribute access; retiring _FlattenedTransaction iteration also closes a scout-found open hazard (2-element destructure + save() silently dropped creates/deletes without tripping the version guard).
- Trait conformance test identifies builtin providers explicitly.

6 review findings, 0 must-fix. 1913 tests, ruff clean, self-lint exit 0.
<!-- SECTION:FINAL_SUMMARY:END -->
