---
id: TASK-65
title: 'Transactions CLI: list, show, cancel, and rollback'
status: To Do
assignee: []
created_date: '2026-06-11 15:47'
labels:
  - cli
  - refactor
dependencies:
  - TASK-64
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The architecture doc promises plan/validate/execute/rollback, but there is no CLI to list pending transactions (TransactionStore.list() exists, unused), inspect one, cancel one, or roll back an applied one — even though EditEntry stores the old text precisely to enable rollback. Close the transaction lifecycle loop.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 pypeeker transactions list/show/cancel commands exist with JSON output
- [ ] #2 A rollback command restores pre-apply content using stored old values, with hash verification against the post-apply state and re-indexing of affected files
- [ ] #3 Command help documents the lifecycle; tests cover list/cancel/rollback round-trip
<!-- AC:END -->
