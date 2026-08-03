---
id: TASK-151
title: 'dsl: differential harness and parity manifest'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-08-03 18:10'
updated_date: '2026-08-03 18:43'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 1 of the DSL rewrite (dsl-rewrite.md is normative — read it first). The oracle exists before anything it measures: scripts/differential-check.py runs the OLD engine (frozen, via its CLI JSON output) and the NEW engine over this repo and the test fixtures, compares findings per rule against a parity manifest listing the rules the new engine claims, and fails on any undeclared divergence. The divergence ledger lives in dsl-rewrite.md. With zero claimed rules the harness must pass trivially — that empty-manifest run is the acceptance smoke test. Wire into CI and verify-repo.sh as a fourth step.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 differential-check.py compares old-vs-new findings per rule against a parity manifest, honoring ledger-declared divergences
- [ ] #2 An empty parity manifest passes trivially; a fabricated divergence on a claimed rule fails loudly
- [ ] #3 CI and verify-repo.sh run the harness
- [ ] #4 Full gate green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v4 in worktree /home/user/pypeeker-wt151; orchestrator merges, gates, ships.
<!-- SECTION:PLAN:END -->
