---
id: TASK-82
title: 'check/fix: fix protocol — violations carry plannable fixes'
status: To Do
assignee: []
created_date: '2026-06-11 18:26'
labels:
  - check
  - refactor
  - m2-fixes
dependencies:
  - TASK-74
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Rules emit Violations; refactors emit transactions; nothing connects them. Define a Fix protocol: a violation may carry a fix planner that, given current state, yields EditEntry objects (or declines). Registry plumbing so rules register fixes alongside detection. Foundation for check --fix and the composite planner's intents.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A rule can attach a fix planner to a violation; fix planners produce EditEntries against current file state and may decline (stale)
- [ ] #2 Violations without fixes are unchanged; existing rules untouched
- [ ] #3 Unit tests cover fix production, decline, and the no-fix path
<!-- AC:END -->
