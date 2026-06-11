---
id: TASK-68
title: 'check: impure-function rule with configurable purity policy'
status: To Do
assignee: []
created_date: '2026-06-11 15:47'
labels:
  - check
  - analysis
dependencies:
  - TASK-66
  - TASK-67
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Purity policy tables (IMPURE_BUILTINS, MODULE_IMPURE_NAMES, TYPE_IMPURE_METHODS, ~100 entries) are frozen in code with no config hook, although check already has rule_options. Add an opt-in check rule that flags impure functions matching configured criteria, with policy overridable from pyproject.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 An opt-in check rule flags functions with impurity observations (scope configurable, e.g. by decorator/name-pattern/package)
- [ ] #2 Policy tables can be extended/overridden via [tool.pypeeker.<rule>] options
- [ ] #3 Rule uses the project-scoped rule context (cross-file call graph) and is tested
<!-- AC:END -->
