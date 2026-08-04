---
id: TASK-153
title: 'dsl: port the file-local rule family'
status: Done
assignee:
  - '@claude'
created_date: '2026-08-03 18:11'
updated_date: '2026-08-04 04:27'
labels: []
dependencies:
  - TASK-152
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 3a of the DSL rewrite (dsl-rewrite.md is normative). Port the file-local registry and builtin rules to DSL expressions, claiming each in the parity manifest as it reaches differential parity on this repo plus fixtures. Divergences go in the dsl-rewrite.md ledger, never silently. The old rules are frozen spec: read them sparingly and ranged; their OUTPUT via the old CLI is the ground truth.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Each ported rule is claimed in the parity manifest and differentially identical to the old engine, or its divergence is in the ledger
- [x] #2 Full gate green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v4 in /home/user/pypeeker-wt153. STAGED: prefer-tuple ported and claimed FIRST (first real exercise of the differential oracle — proof-of-life), then the rest of the file-local family, claiming each at parity. TASK-154/155 fan out in parallel worktrees after this merges.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Run wf_7285218a-a28 (died mid-lens after 9 agents; resumed from cache, 14/14 done, ~710k additional subagent tokens).
- Proof of life achieved: scripts/dsl-engine.py + dsl/differential.py + dsl/rules.py; manifest claims prefer-tuple (17-vs-17 genuine comparison, byte-identical) and require-docstrings (0-vs-0 on self due to the visibility-injection quirk; verified 42/42 byte-identical out-of-band on scratch projects; firing behaviour pinned by 8 unit tests including the injection trap).
- Residue and oracle gaps recorded as TASK-158: fan-out rules (docstring-drift), receiver_chain-needing rules (no-argument-mutation, no-hidden-global-mutation), naming-conventions conditional message, the five cross-file rules owned by no phase-3 family, the vacuous-claim fixture target, and the empty-claimed-list guard.
- No ledger entry needed: both claimed rules are byte-identical to the frozen engine.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Made the differential oracle live and landed the first two parity claims.

Changes:
- src/pypeeker/dsl/rules.py: the ported-rule table (rule id -> DSL expression + message template); prefer-tuple reuses library.py's TUPLE_CANDIDATE and reports DECLARED per the ledger's meta-read law; require-docstrings faithfully reproduces the frozen engine's option coercion including the silent-drop quirk that [tool.pypeeker.visibility] injection triggers.
- src/pypeeker/dsl/differential.py + scripts/dsl-engine.py: the new-engine runner — reads the target config without importing check, evaluates claimed rules, emits the JSON findings shape the harness parses.
- scripts/parity-manifest.toml: new-engine argv seam wired; claimed = [prefer-tuple, require-docstrings]. differential-check.py now genuinely compares (prefer-tuple 17-vs-17 on this repo).

Tests: 87 new (3,158 total); four-step gate PASS with the differential step actually comparing.

Follow-ups: TASK-158 (file-local residue, phase-3 ownership gap for five cross-file rules, non-vacuous require-docstrings target, empty-claimed guard).
<!-- SECTION:FINAL_SUMMARY:END -->
