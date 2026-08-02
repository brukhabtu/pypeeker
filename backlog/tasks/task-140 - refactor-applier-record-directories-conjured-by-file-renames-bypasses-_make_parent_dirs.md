---
id: TASK-140
title: >-
  refactor/applier: record directories conjured by file renames (bypasses
  _make_parent_dirs)
status: In Progress
assignee:
  - '@claude'
created_date: '2026-08-02 02:34'
updated_date: '2026-08-02 03:10'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the TASK-137 scout, pre-existing: _apply_file_rename does a bare new_file.parent.mkdir(parents=True, exist_ok=True) instead of going through _make_parent_dirs, so directories conjured by a rename are never recorded — the mid-apply failure handler and rollback both leak them (the under-reach direction of the TASK-137 bug, which fixed the over-reach). Route the rename mkdir through _make_parent_dirs so its directories join created_dirs and the failure-path list.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Directories created by a file rename are recorded in TransactionHeader.created_dirs and removed on rollback when empty
- [ ] #2 A mid-apply failure after a rename staged new directories removes them like create-conjured ones
- [ ] #3 Regression tests cover rename-conjured directory rollback and failure-path cleanup
- [ ] #4 Full gate green (pytest, ruff, self-lint)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v3 in worktree /home/user/pypeeker-wt140 (branch wt/task-140), parallel with TASK-141; orchestrator merges sequentially, gates combined state, ships PRs.
<!-- SECTION:PLAN:END -->
