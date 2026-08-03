---
id: TASK-149
title: 'refactor/cli: demote bypasses the confidence gate that privatize enforces'
status: Done
assignee:
  - '@claude'
created_date: '2026-08-03 14:05'
updated_date: '2026-08-03 19:38'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the TASK-148 usage campaign, proven end-to-end. Given the same finding on the same symbol, privatize refuses and demote proceeds. pypeeker check --strict nominates pypeeker.refactor.batch:DropReason as over-exposed at HEURISTIC confidence. privatize --plan skips it with reason heuristic-confidence and the detail: the nominating finding has heuristic confidence (dynamic access nearby); excluded from auto-fix. demote on the identical symbol planned 11 edits, applied without warning, and broke import collection in 19 test files that reference DropReason. Rollback restored the tree cleanly and tests recovered, so the transaction machinery is sound; the gap is that the single-symbol path never consults the confidence the bulk path treats as decisive. Compounding it: src is the only indexed root, so refs and unused-public-symbol cannot see test references at all, yet the finding text claims the symbol has no references in the project.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 demote refuses or warns on a HEURISTIC-confidence nomination, consistent with privatize
- [x] #2 Regression test drives the DropReason case: demote on a heuristic nomination does not silently produce a breaking rename
- [x] #3 Full gate green
- [x] #4 The warning or refusal names its evidence (heuristic visibility from dynamic access nearby; references outside the indexed roots, e.g. tests/, are not visible to the index)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v4 arc with TASK-150 in worktree /home/user/pypeeker-wt149 (both touch cli.py; bundled to share scout context and avoid worktree conflicts).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Pipeline run wf_b1d6d450-a39 (v4, 7 agents). Scout FALSIFIED the spec mechanism: Symbol.visibility_confidence is dead (heuristic on 9787/9787 symbols; architecture.md records it dead). Implemented the real signal instead: dynamic access in the defining module — semantically identical to what privatize reflects.
- Design: warn-and-proceed, not refuse — a CLI-typed id is deliberate, matching dsl-rewrite.md fork 12 semantics.
- Re-review caught the project-constant banner problem; orchestrator applied the mention-scan fix (advisory reports N of M unindexed files that actually mention the name; silent when none do).
- Shipped as PR #118.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
demote attaches evidence-named advisories (dynamic-access caveat mirroring privatize's heuristic-confidence skip; per-symbol unindexed-mentions caveat) via the existing warnings channel, warn-and-proceed. DropReason regression test-driven end-to-end. Shipped as PR #118 (squash-merged); gate green.
<!-- SECTION:FINAL_SUMMARY:END -->
