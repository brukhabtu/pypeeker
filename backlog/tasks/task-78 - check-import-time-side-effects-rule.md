---
id: TASK-78
title: 'check: import-time-side-effects rule'
status: To Do
assignee: []
created_date: '2026-06-11 18:25'
labels:
  - check
  - analysis
  - m1-advisory
dependencies:
  - TASK-74
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Imports should be free. Module-scope CALL references that hit the purity policy (I/O, network, time, subprocess) or call project functions that are impure make importing the module a side effect. Detectable from in_scope_id == module scope plus the purity policy and call graph.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Rule flags module-scope calls matching the impure builtin/module policy, and module-scope calls to project functions whose impurities() is non-empty
- [ ] #2 Options: allow patterns (e.g. logging.getLogger), extra-impure; opt-in
- [ ] #3 Tests cover direct impure call, impure project-function call, and allowed patterns; dogfood run recorded
<!-- AC:END -->
