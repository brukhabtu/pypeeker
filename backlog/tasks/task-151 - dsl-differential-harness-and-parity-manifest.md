---
id: TASK-151
title: 'dsl: differential harness and parity manifest'
status: Done
assignee:
  - '@claude'
created_date: '2026-08-03 18:10'
updated_date: '2026-08-03 20:25'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 1 of the DSL rewrite (dsl-rewrite.md is normative — read it first). The oracle exists before anything it measures: scripts/differential-check.py runs the OLD engine (frozen, via its CLI JSON output) and the NEW engine over this repo and the test fixtures, compares findings per rule against a parity manifest listing the rules the new engine claims, and fails on any undeclared divergence. The divergence ledger lives in dsl-rewrite.md. With zero claimed rules the harness must pass trivially — that empty-manifest run is the acceptance smoke test. Wire into CI and verify-repo.sh as a fourth step.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 differential-check.py compares old-vs-new findings per rule against a parity manifest, honoring ledger-declared divergences
- [x] #2 An empty parity manifest passes trivially; a fabricated divergence on a claimed rule fails loudly
- [x] #3 CI and verify-repo.sh run the harness
- [x] #4 Full gate green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v4 in worktree /home/user/pypeeker-wt151; orchestrator merges, gates, ships.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Pipeline run wf_6a5e56c2-7dd (v4, 15 agents). Scout falsified the spec interface: check has no --json (text lines are the oracle format), --strict is mandatory, and unknown rule names are silently ignored — the harness grew a registry-backed typo guard because a manifest typo would false-green.
- Scratch-copy materialization probe-proven byte-identical (project-root-relative findings); determinism verified incl. fresh-index; all 22 rules enumerated from live registries.
- Gate is now four steps; empty-manifest smoke and fabricated-divergence failure both test-proven. Shipped as PR #119.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The differential oracle for the DSL rewrite: scripts/differential-check.py + parity-manifest.toml, per-rule old-vs-new comparison with ledger-declared divergences honored, pluggable new-engine seam, registry typo guard. Fourth gate step in verify-repo.sh and CI. Shipped as PR #119 (squash-merged); four-step gate green.
<!-- SECTION:FINAL_SUMMARY:END -->
