---
id: TASK-137
title: 'refactor/applier: rollback removes only directories the transaction created'
status: Done
assignee:
  - '@claude'
created_date: '2026-08-02 01:29'
updated_date: '2026-08-02 02:33'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Rollback undoes file creations then calls _prune_empty_ancestors (applier.py), which walks up from the removed file deleting every now-empty directory below the project root. A directory that existed empty before the transaction is deleted too — the post-rollback tree differs from the pre-apply tree in the opposite direction from the PEP 420 concern the pruning exists to solve. The docstring admits the created-dirs list from _make_parent_dirs is lost between processes; persist it in the transaction record so rollback removes exactly the directories apply created.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Rolling back a transaction that created files inside a pre-existing empty directory leaves that directory in place
- [x] #2 Rolling back still removes directories the transaction itself created, so repeated plan-and-rollback cycles accumulate nothing
- [x] #3 Regression tests cover both the pre-existing-empty-dir and created-dir cases
- [x] #4 Full gate green (pytest, ruff, self-lint)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v3 in worktree /home/user/pypeeker-wt137 (branch wt/task-137); orchestrator merges, gates combined state, ships PR.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Pipeline run wf_d245556d-bb6 (13 agents). Scout found the discriminator already existed (_make_parent_dirs returns exactly the created set, then apply dropped it) and enumerated the one existing test pinning the old behavior.
- Lenses caught a must-fix: recording created_dirs after the commit could strand a mutated tree under a PENDING header; fixed by recording inside the staging loop.
- Final gate flagged the single test inversion under frozen policy; orchestrator adjudicated it sanctioned (the test docstring itself named this fix as the deferred trade-off). All other test changes are additions.
- Legacy on-disk transactions (created_dirs=None) keep old pruning; [] means created-nothing.
- Scout noted pre-existing under-reach: _apply_file_rename bypasses _make_parent_dirs (rename-conjured dirs leak) — candidate follow-up.
- Combined-state gate green after merging with #106; shipped as PR #107.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Rollback now removes exactly the directories the transaction created; shipped as PR #107 (squash-merged).

- TransactionHeader.created_dirs (additive, None=legacy fallback to old pruning, []=created nothing) recorded during staging, consumed by _remove_dirs on rollback.
- One sanctioned test inversion (the test that pinned the old over-pruning as an accepted asymmetry); 300+ added test lines incl. pre-existing-empty-dir survival, repeated-cycle hygiene, legacy fallback.
- storage-transaction-architecture.md and architecture.md updated to the recorded-set mechanism.

Tests: full gate green standalone and on combined state with TASK-136.
<!-- SECTION:FINAL_SUMMARY:END -->
