---
id: TASK-161
title: 'models: remove dead Symbol.visibility_confidence'
status: Done
assignee:
  - '@claude'
created_date: '2026-08-08 19:59'
updated_date: '2026-08-08 23:26'
labels:
  - models
  - binder
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-149 established the field is dead: HEURISTIC on 9,787/9,787 symbols, gates nothing anywhere. Grep confirms no frozen path reads it (models + binder only), so removal is safe pre-flip. Remove the field from Symbol, its assignments in binder, and any serialization; indexes are regenerated locally so no migration. If the scout finds a live consumer after all, stop and report instead of forcing.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Symbol.visibility_confidence is gone and nothing references it
- [x] #2 Full gate green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v4 in /home/user/pypeeker-wt161, off main-with-#128 (sequenced after TASK-159 to avoid binder-file merge conflicts).
<!-- SECTION:PLAN:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Deleted the dead field (PR #129): Symbol.visibility_confidence, its nine binder write sites, and the three fixture kwargs. Verified rather than assumed the compat story: index hashes are over source bytes so old .pypeeker/ dirs are loaded, not regenerated, and from_dict drops unknown keys — pinned by a new Symbol-level tolerance test doubling as the reintroduction guard. CLI JSON loses the always-heuristic key. architecture.md verdict row updated to Removed. Follow-up recorded, not actioned: get_visibility's confidence half is now read only by its own frozen tests. Gate PASS in worktree and after merge; both lens rounds clean.
<!-- SECTION:FINAL_SUMMARY:END -->
