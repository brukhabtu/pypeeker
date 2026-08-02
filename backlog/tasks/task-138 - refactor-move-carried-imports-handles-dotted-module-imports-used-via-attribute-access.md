---
id: TASK-138
title: >-
  refactor/move: carried-imports handles dotted module imports used via
  attribute access
status: In Progress
assignee:
  - '@claude'
created_date: '2026-08-02 01:29'
updated_date: '2026-08-02 04:58'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The carried-imports analysis behind move-symbol (preconditions.py CarriedImportsUnconditional and the move planner) misses the dotted-module form: import os.path binds the name os, and a moved body using os.path.join reaches the submodule purely through attribute access, so the dependency is not detected as needing to be carried or refused. Scout must characterize the exact miss and its blast radius before implementation.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Moving a symbol whose body uses a dotted-module import via attribute access either carries the import correctly or refuses with a named precondition — never silently drops the dependency
- [ ] #2 Regression tests cover the import os.path attribute-access pattern for both the carry and refusal paths
- [ ] #3 Full gate green (pytest, ruff, self-lint)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v3 on the working branch (sole mutating run); scout must characterize the exact miss before implementation; orchestrator gates, ships PR, bookkeeps.
<!-- SECTION:PLAN:END -->
