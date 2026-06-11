---
id: TASK-64
title: >-
  Dead surfaces: implement or remove FileIndex.errors and TransactionStatus
  lifecycle
status: To Do
assignee: []
created_date: '2026-06-11 15:47'
labels:
  - models
  - refactor
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
FileIndex.errors is never populated (the binder never appends to state.errors; indexing errors go to IndexResult.errors), and TransactionStatus.APPLIED/FAILED/ROLLED_BACK can never occur because the applier deletes the transaction file on success and leaves PENDING on failure. Both invite readers to trust fields that are never written. Decide per surface: implement it for real or remove it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 FileIndex.errors is either populated by the binder (e.g. parse/bind recoverable errors) and surfaced, or removed from the model and serialization
- [ ] #2 Transaction status transitions are real (status updated on apply/failure instead of file deletion, or enum trimmed to what actually happens); decision recorded in the task notes
- [ ] #3 Serialization forward-compat handled for any removed/added fields; full test suite passes
<!-- AC:END -->
