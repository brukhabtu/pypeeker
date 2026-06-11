---
id: TASK-80
title: 'check: unused-return-value rule (+ binder call-result-used fact)'
status: To Do
assignee: []
created_date: '2026-06-11 18:26'
labels:
  - check
  - binder
  - m1-advisory
dependencies:
  - TASK-74
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
A function with a declared non-None return type whose result is discarded at every call site is a procedure pretending to be a function, or every caller is buggy. Needs one new binder fact: whether a CALL reference is a bare expression statement. Scope conservatively to declared non-None returns.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Binder records result_used (or equivalent) on CALL references — false when the call is a bare expression statement
- [ ] #2 Project rule flags FUNCTION/METHOD symbols with a declared non-None return annotation whose every project call site discards the result
- [ ] #3 Index serialization forward-compatible; tests cover used/discarded/mixed and None-returning exclusion; opt-in
<!-- AC:END -->
