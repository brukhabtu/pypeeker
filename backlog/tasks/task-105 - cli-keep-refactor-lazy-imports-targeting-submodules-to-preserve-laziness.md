---
id: TASK-105
title: 'cli: keep refactor lazy imports targeting submodules to preserve laziness'
status: To Do
assignee: []
created_date: '2026-07-03 01:57'
labels:
  - cli
  - performance
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The barrel migration pointed cli.py's function-level lazy imports at the pypeeker.refactor barrel, whose __init__ eagerly imports all 8 refactor submodules. A single subcommand (e.g. plan-inline-variable) now loads applier/batch/planner/privatize/etc. it never uses, defeating the point of the lazy import. Point cli.py's function-local refactor imports back at the specific submodule while keeping the curated barrel for external/test consumers. Surfaced as a skipped finding in the architecture-review code review.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Function-level imports in cli.py import from the specific refactor submodule (e.g. from pypeeker.refactor.extract import ...), not the package barrel
- [ ] #2 The refactor barrel is retained for external/test consumers; module-level barrel imports elsewhere are unaffected
- [ ] #3 Tests, ruff, and the self-check all pass
<!-- AC:END -->
