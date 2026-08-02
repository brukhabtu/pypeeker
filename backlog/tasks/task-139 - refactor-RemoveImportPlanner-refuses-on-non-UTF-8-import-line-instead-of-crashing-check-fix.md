---
id: TASK-139
title: >-
  refactor: RemoveImportPlanner refuses on non-UTF-8 import line instead of
  crashing check --fix
status: In Progress
assignee:
  - '@claude'
created_date: '2026-08-02 02:02'
updated_date: '2026-08-02 02:34'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the TASK-136 verification lens, pre-existing: imports_ops.py line 219 (RemoveImportPlanner.plan) does state.line.decode(utf-8) raw, so pypeeker check --fix on a file whose flagged import line contains non-UTF-8 bytes exits with an uncaught UnicodeDecodeError before flatten_store is reached — the flatten-failed refusal mapping in app/check_fixes.py never fires. Mirror the structured-refusal pattern TASK-136 applied in extract.py and batch.py.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 check --fix over a file whose import line is not valid UTF-8 produces a structured refusal, not a traceback
- [ ] #2 Regression test covers the non-UTF-8 import line via the check --fix path
- [ ] #3 Full gate green (pytest, ruff, self-lint)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v3 on the working branch (no worktree; sole mutating run); orchestrator gates, ships PR, bookkeeps.
<!-- SECTION:PLAN:END -->
