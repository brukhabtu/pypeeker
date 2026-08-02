---
id: TASK-136
title: >-
  refactor: UTF-8 hardening — ExtractVariable selection and batch-flatten region
  decodes refuse instead of crash
status: In Progress
assignee:
  - '@claude'
created_date: '2026-08-02 01:29'
updated_date: '2026-08-02 01:29'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Two residual raw-decode sites in refactor/ crash with UnicodeDecodeError on non-UTF-8 content instead of producing a structured refusal. The extract-method path already has the DecodableSelection precondition pattern (extract.py raises ExtractMethodError with the precondition name); the extract-variable path and the batch-flatten materializer (batch.py around lines 1452-1481, whole-file and region decodes) lack the equivalent guard. TASK-133 established the refusal pattern for simulation; mirror it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Planning extract-variable over a non-UTF-8 selection yields a structured refusal naming the precondition, not a traceback
- [ ] #2 Batch flatten over a file or region that does not decode as UTF-8 refuses cleanly with a structured error, not a traceback
- [ ] #3 Regression tests cover both sites with non-UTF-8 fixtures
- [ ] #4 Full gate green (pytest, ruff, self-lint)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v3 in worktree /home/user/pypeeker-wt136 (branch wt/task-136); orchestrator merges, gates combined state, ships PR.
<!-- SECTION:PLAN:END -->
