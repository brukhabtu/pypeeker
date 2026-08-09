---
id: TASK-158
title: 'dsl: port the file-local residue and close the phase-3a oracle gaps'
status: Done
assignee:
  - '@claude'
created_date: '2026-08-04 04:26'
updated_date: '2026-08-09 02:33'
labels:
  - dsl
dependencies:
  - TASK-153
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Continuation of TASK-153 (phase 3a shipped prefer-tuple + require-docstrings with the oracle genuinely comparing). Residue with recorded blockers: docstring-drift (fan-out — N findings per symbol; DSL emits one Match per row), no-argument-mutation and no-hidden-global-mutation (need receiver_chain on the references universe and enclosing-function symbol lookup), naming-conventions (conditional message extension from the per-kind convention table), unused-imports (left to ruff in the gate but still in the old registry). Ownership gap found by the scout: star-imports, unused-return-value, barrel-only, over-exposed-export, under-exposed-access are cross-file and named in neither TASK-154 nor TASK-155 — assign them a family. Oracle gaps: require-docstrings grades 0-vs-0 on the self target because the [tool.pypeeker.visibility] injection empties the rule's visibility set via silent _as_enum_set drops (add a fixture target without that section so the claim is non-vacuous — the port itself was verified 42/42 byte-identical out-of-band); differential-check.py still exits 0 with a PASS banner on an empty claimed list (tighten to an error now that phase 3 has begun). Post-flip candidate recorded: the visibility injection making require-docstrings dead on any project declaring [tool.pypeeker.visibility] is a real frozen-engine bug; fix after flip with a ledger entry.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Every old-registry rule is owned by a phase-3 family task or this one, with no unassigned residue
- [x] #2 require-docstrings (and any similarly vacuous claim) is graded against a target where the old engine emits nonzero findings
- [x] #3 differential-check.py fails loudly on an empty claimed list
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v4 in /home/user/pypeeker-wt158, parallel with TASK-165 (file partition: 158 owns rules.py/manifest/differential-check/sweeps + any fan-out extension in selection.py; 165 owns expr.py Compare guards, adapters, universes._Env/visibility candidate clauses).
<!-- SECTION:PLAN:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Closed the phase-3a residue and oracle gaps (PR #131). 14 of 22 rules claimed; the oracle can no longer pass vacuously.

- Oracle: empty claimed list is exit 2 (--allow-empty-claim escape); filelocal fixture target gives require-docstrings its first nonzero grade; every claimed rule grades nonzero somewhere or carries a manifest comment naming its pinning tests.
- Ports at byte parity: naming-conventions (conditional suggested-name message; the unparseable-kinds empty-set trap reproduced), unused-imports, and docstring-drift — the fan-out blocker dissolved: drift_rows is a fact_source with per-(function,param) anchors, zero selection/lattice changes.
- Ownership of the 8 unclaimed recorded: 3d cross-file residue (star-imports/barrel-only/under-exposed-access/unused-return-value — expressible today), 3e mutation pair (needs receiver_chain), 3f impurity pair (IMPURITY tier; import-time-side-effects also needs receiver_chain). Corrections: barrel-only was NOT already ported; star-imports is project-scoped.
- Orchestrator post-lens fixes: privatized two helpers, honestly scoped the fact-source anchor-uniqueness claim, tempered --why wording, emptied-manifest diagnostic named.

Tests: 99 new (3,528 total); gate PASS in worktree and after merge.
<!-- SECTION:FINAL_SUMMARY:END -->
