---
id: TASK-123
title: 'refactor: everything-is-a-batch (single execution path)'
status: To Do
assignee:
  - '@claude'
created_date: '2026-07-31 04:51'
labels:
  - refactor
  - architecture
dependencies:
  - TASK-122
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 3: single-op CLI commands (plan-rename, plan-extract-*, plan-inline) route through the batch engine as a batch of one; the direct-planner call path is removed from cli/app. Output contracts preserved.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 All mutating CLI entry points build intents and go through schedule->materialize->transaction; direct planner invocation from cli/app is gone.
- [ ] #2 JSON output shapes and exit codes unchanged (frozen contract); full gate green.
<!-- AC:END -->
