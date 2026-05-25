---
id: TASK-51
title: 'Refactor foundation: INSERT/DELETE transaction edit operations'
status: To Do
assignee: []
created_date: '2026-05-25 13:01'
labels:
  - refactor
  - foundation
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Generalize the transaction edit model beyond REPLACE so complex refactorings can express structural changes. Add INSERT (at a byte offset) and DELETE (a byte range) ops to EditEntry/EditOp; the applier applies and rolls them back, applying edits in descending byte order so offsets stay valid; hash-based conflict detection and atomic apply/rollback continue to work. Rename (REPLACE) is unchanged.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 EditOp gains INSERT and DELETE; EditEntry supports them (INSERT has an insertion point + text; DELETE has a byte range)
- [ ] #2 TransactionApplier applies INSERT/DELETE correctly (edits applied in descending offset order within a file) and rolls them back on failure
- [ ] #3 Existing REPLACE/rename behavior and conflict detection are unchanged; tests cover insert, delete, and mixed edits round-tripping
<!-- AC:END -->
