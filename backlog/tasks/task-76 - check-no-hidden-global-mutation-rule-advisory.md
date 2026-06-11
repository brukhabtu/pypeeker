---
id: TASK-76
title: 'check: no-hidden-global-mutation rule (advisory)'
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
pylint flags the 'global' keyword; the real hazards are silent: mutating module-level containers from functions (receiver root resolves to a module-scope VARIABLE), attribute writes on imported modules, os.environ mutation. All visible to the existing receiver/write facts; no new analysis required.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Rule flags outer-scope writes targeting module scope, mutator calls on module-level variables, and attribute writes on IMPORT receivers, from inside functions
- [ ] #2 Options: allow patterns; opt-in
- [ ] #3 Tests cover each shape plus non-flagged local mutations; dogfood run recorded
<!-- AC:END -->
