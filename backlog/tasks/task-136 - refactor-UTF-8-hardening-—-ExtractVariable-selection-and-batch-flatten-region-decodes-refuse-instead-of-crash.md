---
id: TASK-136
title: >-
  refactor: UTF-8 hardening — ExtractVariable selection and batch-flatten region
  decodes refuse instead of crash
status: Done
assignee:
  - '@claude'
created_date: '2026-08-02 01:29'
updated_date: '2026-08-02 02:02'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Two residual raw-decode sites in refactor/ crash with UnicodeDecodeError on non-UTF-8 content instead of producing a structured refusal. The extract-method path already has the DecodableSelection precondition pattern (extract.py raises ExtractMethodError with the precondition name); the extract-variable path and the batch-flatten materializer (batch.py around lines 1452-1481, whole-file and region decodes) lack the equivalent guard. TASK-133 established the refusal pattern for simulation; mirror it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Planning extract-variable over a non-UTF-8 selection yields a structured refusal naming the precondition, not a traceback
- [x] #2 Batch flatten over a file or region that does not decode as UTF-8 refuses cleanly with a structured error, not a traceback
- [x] #3 Regression tests cover both sites with non-UTF-8 fixtures
- [x] #4 Full gate green (pytest, ruff, self-lint)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v3 in worktree /home/user/pypeeker-wt136 (branch wt/task-136); orchestrator merges, gates combined state, ships PR.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Pipeline run wf_f557fab8-03a (6 agents, lean shape). Scout probes found TWO extract-variable decode sites (expression text + indentation slice) and confirmed all four flatten sites reachable; also proved ASCII-selection-in-non-UTF-8-file must keep working, which ruled out a whole-file guard.
- Lens found pre-existing third site imports_ops.py:219 (RemoveImportPlanner) crashing check --fix — filed separately as TASK-139.
- Orchestrator gated independently via scripts/verify-repo.sh (all PASS); shipped as PR #106.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Structured UTF-8 refusals for extract-variable and batch flatten; shipped as PR #106 (squash-merged).

- extract.py: both reachable decode spans guarded with the SourceIsUtf8-family precondition; refusals carry the precondition name through the frozen plan-refused envelope; ASCII selections in non-UTF-8 files still apply byte-identically (probe-pinned).
- batch.py: all four flatten_store decodes raise FlattenError naming the file, through the existing flatten-failed envelope.
- 170 added test lines, zero existing test lines modified.

Tests: full gate green (pytest, ruff, self-lint zero findings).
<!-- SECTION:FINAL_SUMMARY:END -->
