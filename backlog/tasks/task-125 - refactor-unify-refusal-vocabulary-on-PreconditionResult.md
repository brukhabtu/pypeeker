---
id: TASK-125
title: 'refactor: unify refusal vocabulary on PreconditionResult'
status: To Do
assignee:
  - '@claude'
created_date: '2026-07-31 04:51'
labels:
  - refactor
  - architecture
dependencies:
  - TASK-124
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 5: PreconditionResult is the atom of refusal; DropReason.PRECONDITION_FAILED carries the named failing precondition; decline paths report the same shape everywhere.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Batch drops and plan refusals carry the named precondition; CLI refusal output unified; full gate green.
<!-- AC:END -->
