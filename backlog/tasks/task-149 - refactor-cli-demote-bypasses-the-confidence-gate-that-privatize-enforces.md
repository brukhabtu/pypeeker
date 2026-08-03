---
id: TASK-149
title: 'refactor/cli: demote bypasses the confidence gate that privatize enforces'
status: To Do
assignee: []
created_date: '2026-08-03 14:05'
updated_date: '2026-08-03 18:12'
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
- [ ] #2 The unused-public-symbol finding text no longer claims project-wide absence of references when the index scope cannot support that claim
- [ ] #3 Regression test drives the DropReason case: demote on a heuristic nomination does not silently produce a breaking rename
- [ ] #4 Full gate green
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
DSL rewrite program (dsl-rewrite.md): phase 4 makes this structural — one shared mutation value with the floor as an attribute. Still valid as a standalone near-term fix (cli.py demote path is NOT a frozen oracle path); closes at the flip if not before.
<!-- SECTION:NOTES:END -->
