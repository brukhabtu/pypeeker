---
id: TASK-61
title: 'Applier: surface re-index failures instead of swallowing them'
status: To Do
assignee: []
created_date: '2026-06-11 15:46'
labels:
  - refactor
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TransactionApplier._reindex_files catches every exception with a bare pass, so a failed re-index after a successful apply leaves the on-disk index silently inconsistent — and the rest of the system trusts the index. Failures must be reported.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Re-index failures are collected and returned in the apply JSON output (e.g. files_reindex_failed with error messages), not swallowed
- [ ] #2 Apply still succeeds (edits are already on disk) but the output makes index inconsistency visible
- [ ] #3 Test covers a re-index failure surfacing in the result
<!-- AC:END -->
