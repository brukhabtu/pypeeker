---
id: TASK-141
title: >-
  refactor: DeleteSymbolPlanner UTF-8 refusal + close the raw-decode family with
  a sweep
status: Done
assignee:
  - '@claude'
created_date: '2026-08-02 03:10'
updated_date: '2026-08-02 04:58'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Fourth member of the UTF-8 crash family, probe-verified by the TASK-139 lens round: refactor/delete.py line 149 DeleteSymbolPlanner.plan does content[state.start:end].decode(utf-8) raw, so check --fix with unused-public-symbol remedies (also-private=true) crashes with a bare traceback and empty stdout on a non-UTF-8 file. Beyond fixing this site in the established SourceIsUtf8 pattern (TASK-136/139 precedents), sweep ALL of src/ for remaining raw .decode(utf-8) calls reachable from CLI or check --fix paths and either guard them in-family or record with evidence why each is unreachable/safe — this family should be closed by search, not discovered one lens round at a time.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 check --fix driving a delete-symbol remedy over a non-UTF-8 region produces a structured refusal, not a traceback
- [x] #2 A recorded sweep of raw decode sites across src/ exists; every reachable site is guarded, every remaining one has a stated reason it is safe
- [x] #3 Regression tests cover the delete-symbol path with non-UTF-8 fixtures
- [x] #4 Full gate green (pytest, ruff, self-lint)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v3 in worktree /home/user/pypeeker-wt141 (branch wt/task-141), parallel with TASK-140; orchestrator merges sequentially, gates combined state, ships PRs.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Pipeline run wf_2c58e476-bd6 (14 agents, 712 tool uses — the sweep earned the heavier shape). Sweep found FIVE reachable crash sites (delete, inline x2, MovedBodyClosed raising out of evaluate(), move destination+importer spans), two gratuitous decodes made byte comparisons, two message decodes made errors=replace.
- Fix-audit lens caught a residual move.py importer-span site in re-review; fixed in-run with file-absolute byte offsets.
- Sweep record landed in architecture.md — Raw-decode sweep (TASK-141); every remaining raw decode has a stated safety argument.
- Shipped as PR #110; gate green standalone and combined (1993 tests).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Closed the UTF-8 raw-decode crash family by sweep; shipped as PR #110 (squash-merged).

- Five probe-verified reachable sites guarded span-scoped via SourceIsUtf8 through existing error channels (plan-refused envelope, no new JSON fields).
- Two compare-only decodes became byte comparisons; two message decodes became errors=replace.
- architecture.md records the sweep with safety arguments for every remaining raw decode.
- 973 added test lines, zero existing test lines modified.

Tests: full gate green standalone and combined (1993 passed).
<!-- SECTION:FINAL_SUMMARY:END -->
