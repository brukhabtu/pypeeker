---
id: TASK-149
title: 'refactor/cli: demote bypasses the confidence gate that privatize enforces'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-08-03 14:05'
updated_date: '2026-08-03 18:43'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the TASK-148 usage campaign, proven end-to-end. Given the same finding on the same symbol, privatize refuses and demote proceeds. pypeeker check --strict nominates pypeeker.refactor.batch:DropReason as over-exposed at HEURISTIC confidence. privatize --plan skips it with reason heuristic-confidence and the detail: the nominating finding has heuristic confidence (dynamic access nearby); excluded from auto-fix. demote on the identical symbol planned 11 edits, applied without warning, and broke import collection in 19 test files that reference DropReason. Rollback restored the tree cleanly and tests recovered, so the transaction machinery is sound; the gap is that the single-symbol path never consults the confidence the bulk path treats as decisive. Compounding it: src is the only indexed root, so refs and unused-public-symbol cannot see test references at all, yet the finding text claims the symbol has no references in the project.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 demote refuses or warns on a HEURISTIC-confidence nomination, consistent with privatize
- [ ] #2 Regression test drives the DropReason case: demote on a heuristic nomination does not silently produce a breaking rename
- [ ] #3 Full gate green
- [ ] #4 The warning or refusal names its evidence (heuristic visibility from dynamic access nearby; references outside the indexed roots, e.g. tests/, are not visible to the index)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v4 arc with TASK-150 in worktree /home/user/pypeeker-wt149 (both touch cli.py; bundled to share scout context and avoid worktree conflicts).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
DSL rewrite program (dsl-rewrite.md): phase 4 makes this structural — one shared mutation value with the floor as an attribute. Still valid as a standalone near-term fix (cli.py demote path is NOT a frozen oracle path); closes at the flip if not before.

AC rescoped before launch: the finding-text over-claim fix lives in frozen rules.py and is already covered by the rewrite (message templates freely changeable at flip, dsl-rewrite.md fork 13). Interim fix stays entirely on unfrozen paths (cli/app).
<!-- SECTION:NOTES:END -->
