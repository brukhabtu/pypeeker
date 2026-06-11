---
id: TASK-91
title: 'fix: naming-convention rule + cross-module convention renames'
status: To Do
assignee: []
created_date: '2026-06-11 18:27'
labels:
  - fix
  - m4-program-fixes
dependencies:
  - TASK-84
  - TASK-89
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
snake_case functions, PascalCase classes, UPPER_CASE module constants — detectable from symbol kind+name; fixable only by a barrel-aware, confidence-gated, whole-program rename. Batch renames ride the composite planner (id-changing intents, collision handling).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Rule flags convention violations per kind with configurable conventions/allowlist
- [ ] #2 Fixes plan cross-module renames (exports per policy flag) gated to declared/direct resolution; collisions drop with reasons
- [ ] #3 End-to-end batch test renaming multiple symbols incl. two that would collide naively
<!-- AC:END -->
