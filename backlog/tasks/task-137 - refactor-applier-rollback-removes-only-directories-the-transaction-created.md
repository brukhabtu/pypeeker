---
id: TASK-137
title: 'refactor/applier: rollback removes only directories the transaction created'
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
Rollback undoes file creations then calls _prune_empty_ancestors (applier.py), which walks up from the removed file deleting every now-empty directory below the project root. A directory that existed empty before the transaction is deleted too — the post-rollback tree differs from the pre-apply tree in the opposite direction from the PEP 420 concern the pruning exists to solve. The docstring admits the created-dirs list from _make_parent_dirs is lost between processes; persist it in the transaction record so rollback removes exactly the directories apply created.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Rolling back a transaction that created files inside a pre-existing empty directory leaves that directory in place
- [ ] #2 Rolling back still removes directories the transaction itself created, so repeated plan-and-rollback cycles accumulate nothing
- [ ] #3 Regression tests cover both the pre-existing-empty-dir and created-dir cases
- [ ] #4 Full gate green (pytest, ruff, self-lint)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v3 in worktree /home/user/pypeeker-wt137 (branch wt/task-137); orchestrator merges, gates combined state, ships PR.
<!-- SECTION:PLAN:END -->
