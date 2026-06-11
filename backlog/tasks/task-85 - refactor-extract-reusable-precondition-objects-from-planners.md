---
id: TASK-85
title: 'refactor: extract reusable precondition objects from planners'
status: To Do
assignee: []
created_date: '2026-06-11 18:27'
labels:
  - refactor
  - m3-planner
dependencies: []
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Rename/extract/inline planners encode preconditions as inline raises (single assignment, no escape, purity, name conflicts, staleness). Extract them into named, independently evaluable precondition objects so the composite planner can re-validate guarded intents at materialization time.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Preconditions are first-class objects evaluable against current state, returning pass/fail+reason
- [ ] #2 Existing planners consume them with identical error messages and behavior (suite green, no test edits beyond imports)
- [ ] #3 Each existing planner's precondition set is enumerable for reuse by the batch scheduler
<!-- AC:END -->
