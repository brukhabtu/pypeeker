---
id: TASK-141
title: >-
  refactor: DeleteSymbolPlanner UTF-8 refusal + close the raw-decode family with
  a sweep
status: In Progress
assignee:
  - '@claude'
created_date: '2026-08-02 03:10'
updated_date: '2026-08-02 03:10'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Fourth member of the UTF-8 crash family, probe-verified by the TASK-139 lens round: refactor/delete.py line 149 DeleteSymbolPlanner.plan does content[state.start:end].decode(utf-8) raw, so check --fix with unused-public-symbol remedies (also-private=true) crashes with a bare traceback and empty stdout on a non-UTF-8 file. Beyond fixing this site in the established SourceIsUtf8 pattern (TASK-136/139 precedents), sweep ALL of src/ for remaining raw .decode(utf-8) calls reachable from CLI or check --fix paths and either guard them in-family or record with evidence why each is unreachable/safe — this family should be closed by search, not discovered one lens round at a time.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 check --fix driving a delete-symbol remedy over a non-UTF-8 region produces a structured refusal, not a traceback
- [ ] #2 A recorded sweep of raw decode sites across src/ exists; every reachable site is guarded, every remaining one has a stated reason it is safe
- [ ] #3 Regression tests cover the delete-symbol path with non-UTF-8 fixtures
- [ ] #4 Full gate green (pytest, ruff, self-lint)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v3 in worktree /home/user/pypeeker-wt141 (branch wt/task-141), parallel with TASK-140; orchestrator merges sequentially, gates combined state, ships PRs.
<!-- SECTION:PLAN:END -->
