---
id: TASK-139
title: >-
  refactor: RemoveImportPlanner refuses on non-UTF-8 import line instead of
  crashing check --fix
status: Done
assignee:
  - '@claude'
created_date: '2026-08-02 02:02'
updated_date: '2026-08-02 03:10'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the TASK-136 verification lens, pre-existing: imports_ops.py line 219 (RemoveImportPlanner.plan) does state.line.decode(utf-8) raw, so pypeeker check --fix on a file whose flagged import line contains non-UTF-8 bytes exits with an uncaught UnicodeDecodeError before flatten_store is reached — the flatten-failed refusal mapping in app/check_fixes.py never fires. Mirror the structured-refusal pattern TASK-136 applied in extract.py and batch.py.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 check --fix over a file whose import line is not valid UTF-8 produces a structured refusal, not a traceback
- [x] #2 Regression test covers the non-UTF-8 import line via the check --fix path
- [x] #3 Full gate green (pytest, ruff, self-lint)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v3 on the working branch (no worktree; sole mutating run); orchestrator gates, ships PR, bookkeeps.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Pipeline run wf_0dc8d6b0-9ed (6 agents, lean). Scout probe-pinned that whole-line and whole-file guards would break two working behaviors; fix is span-scoped to exactly the recorded EditEntry.old bytes.
- Refusal surfaces as declined entry with generic plan-refused (SourceIsUtf8 has no legacy slug; never-hardcode-slug invariant honored).
- Lenses found a FOURTH family member: delete.py:149 DeleteSymbolPlanner, probe-verified crashing check --fix via unused-public-symbol remedies — filed as TASK-141 with a systematic sweep AC.
- Shipped as PR #108; gate green (pytest, ruff, self-lint).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
RemoveImportPlanner refuses structurally on non-UTF-8 spans instead of crashing check --fix; shipped as PR #108 (squash-merged).

- Both decode sites guarded via SourceIsUtf8 on exactly the span recorded; refusal flows the existing RemoveImportError channel to a plan-refused declined entry; no new JSON fields.
- Span scoping probe-proven: multi-name removal on lines with non-UTF-8 comments and fixes in files with non-UTF-8 bytes elsewhere keep working.
- 139 added test lines (end-to-end check --fix + planner ports), zero existing lines modified.

Tests: full gate green.
<!-- SECTION:FINAL_SUMMARY:END -->
