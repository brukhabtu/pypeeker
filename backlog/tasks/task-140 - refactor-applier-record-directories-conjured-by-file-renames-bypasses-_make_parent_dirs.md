---
id: TASK-140
title: >-
  refactor/applier: record directories conjured by file renames (bypasses
  _make_parent_dirs)
status: Done
assignee:
  - '@claude'
created_date: '2026-08-02 02:34'
updated_date: '2026-08-02 03:56'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the TASK-137 scout, pre-existing: _apply_file_rename does a bare new_file.parent.mkdir(parents=True, exist_ok=True) instead of going through _make_parent_dirs, so directories conjured by a rename are never recorded — the mid-apply failure handler and rollback both leak them (the under-reach direction of the TASK-137 bug, which fixed the over-reach). Route the rename mkdir through _make_parent_dirs so its directories join created_dirs and the failure-path list.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Directories created by a file rename are recorded in TransactionHeader.created_dirs and removed on rollback when empty
- [x] #2 A mid-apply failure after a rename staged new directories removes them like create-conjured ones
- [x] #3 Regression tests cover rename-conjured directory rollback and failure-path cleanup
- [x] #4 Full gate green (pytest, ruff, self-lint)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v3 in worktree /home/user/pypeeker-wt140 (branch wt/task-140), parallel with TASK-141; orchestrator merges sequentially, gates combined state, ships PRs.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Pipeline run wf_87a278b0-74c (8 agents). Scout probe-confirmed both leaks and that rename rollback itself was otherwise correct; also caught a purity-self-test timing trap (skips without index, fails after).
- Implementation predicts-and-records at the call site rather than relocating the mkdir, keeping the frozen purity test addition-only; re-review noted the call-site-convention coupling — accepted with docstrings naming the convention.
- Bonus hardening: escaping absolute rename destinations now fail the apply instead of silently moving files outside the project.
- Orchestrator fixed two residual stale docstrings (transaction.py / transaction_store.py claiming created_dirs comes exactly from _make_parent_dirs).
- Shipped as PR #109; gate green standalone and combined.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Rename-conjured directories now join created_dirs and are cleaned on rollback and failure paths; shipped as PR #109 (squash-merged).

- apply records predicted-missing parents at the rename call site into the same accumulation creations use; no dedup needed (probe-proven ordering).
- Escaping absolute rename destinations fail the apply, matching the creation twin.
- 204 added lifecycle-test lines + one added purity parametrize line; zero existing test lines modified.

Tests: full gate green standalone and on combined branch state.
<!-- SECTION:FINAL_SUMMARY:END -->
