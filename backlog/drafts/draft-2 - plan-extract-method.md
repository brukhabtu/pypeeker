---
id: DRAFT-2
title: plan-extract-method
status: Draft
assignee: []
created_date: '2026-05-25 13:02'
labels: []
dependencies:
  - TASK-51
  - TASK-52
  - TASK-53
priority: low
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Extract a statement range into a new function/method. Uses range data-flow for parameters (read-before-defined) and return values (defined-in-range-read-after), refuses when control flow escapes the range (break/continue/return), synthesizes the def + call on the CST, and emits a transaction.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Extracts a range into a new function with correct params/returns; replaces the range with a call
- [ ] #2 Refuses on control-flow escape or name conflicts; tests cover a clean extract end-to-end
<!-- AC:END -->
