---
id: TASK-122
title: 'refactor: planner registry (@register_planner) replaces _materialize dispatch'
status: To Do
assignee:
  - '@claude'
created_date: '2026-07-31 04:51'
labels:
  - refactor
  - architecture
dependencies:
  - TASK-121
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 2: mirror @register_rule with @register_planner(intent_kind); batch._materialize becomes a registry lookup; planners self-register. Behavior-preserving.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A planner registry exists; each existing intent kind (rename, inline, extract-variable, extract-method, edit/fix, delete-symbol) resolves through it; the isinstance chain in _materialize is gone.
- [ ] #2 Unknown-kind handling preserved as a registry miss; full gate green.
<!-- AC:END -->
