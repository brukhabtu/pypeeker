---
id: TASK-97
title: 'fix: mass-demotion plans via the batch planner'
status: To Do
assignee: []
created_date: '2026-06-11 18:28'
labels:
  - fix
  - visibility
  - m5-visibility
dependencies:
  - TASK-89
  - TASK-96
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Apply the visibility principle to a whole codebase: over-exposed findings become demotion intents scheduled by the composite planner (id-changing ops, collision handling, guarded re-validation). Acceptance: a demotion plan over pypeeker's own src that the suite survives.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Visibility findings convert to demote intents; plan-batch schedules and flattens them with drops reported
- [ ] #2 Collisions (existing _name) and hierarchy/public-root refusals drop cleanly with reasons
- [ ] #3 Dogfood: batch demotion plan over pypeeker applied on a scratch branch with the full suite passing; results recorded in notes
<!-- AC:END -->
