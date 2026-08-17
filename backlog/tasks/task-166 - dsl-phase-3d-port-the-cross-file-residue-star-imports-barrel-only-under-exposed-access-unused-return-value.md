---
id: TASK-166
title: >-
  dsl phase 3d: port the cross-file residue (star-imports, barrel-only,
  under-exposed-access, unused-return-value)
status: Done
assignee:
  - '@claude'
created_date: '2026-08-09 02:33'
updated_date: '2026-08-09 21:13'
labels:
  - dsl
dependencies:
  - TASK-158
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The four unclaimed rules the TASK-158 ownership record marked expressible with today's surface (projected sets + fact_source sweeps): star-imports (project-scoped — it consults every module's indexes, correcting TASK-153's file-local classification), barrel-only (NOT previously ported — dsl/visibility.py's BARREL_EXPORTS is only the exemption set; the rule flags cross-package deep imports resolved through re-export chains), under-exposed-access (private reach-ins from other modules), unused-return-value (resolver-driven result_used analysis). Claim each only at genuine differential parity; fixture targets with real violations per the TASK-158 pattern; divergences to the ledger.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Each of the four rules is claimed at parity or carries a ledger divergence
- [x] #2 Each grades nonzero on some target or carries a manifest comment naming its pinning tests
- [x] #3 Full gate green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v4 in /home/user/pypeeker-wt166 (solo run, no parallel worktree). Fixture targets required for star-imports and barrel-only (both zero-on-self: one via ruff delegation, one via the gated clean set); under-exposed-access and unused-return-value grade nonzero on self already.
<!-- SECTION:PLAN:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Ported all four cross-file residue rules at byte parity (PR #133); manifest at 18/22 over 6 targets.

- under-exposed-access 77-vs-77 and unused-return-value 11-vs-11 on self; star-imports and barrel-only graded on the new crossfile fixture (all four frozen star-import message shapes, re-export-chain barrel violations, the accessed-from-tests wording, a result_used escape).
- Frozen subtleties reproduced: last-index-wins module election, first-star-wins name attribution, aliased-import message quoting imported_from's last segment, the eight-pattern default test globs, the no-length dunder test; weakening inventory stays closed at five.
- Remedy divergence ledgered (DSL Finding carries no remedy; RewriteStarImportIntent is a flip concern).
- Orchestrator post-lens fixes: fixture comment path + collection-constraint contradiction, two ledger counts.

Gate PASS in worktree and after merge.
<!-- SECTION:FINAL_SUMMARY:END -->
