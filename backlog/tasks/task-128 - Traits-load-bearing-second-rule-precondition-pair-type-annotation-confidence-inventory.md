---
id: TASK-128
title: >-
  Traits load-bearing: second rule/precondition pair (type-annotation) +
  confidence inventory
status: In Progress
assignee: []
created_date: '2026-07-31 22:25'
updated_date: '2026-07-31 22:31'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan C in roadmap-plans.md (normative, incl. sequencing adjustments). Make the trait abstraction load-bearing: a second provider (type-annotation) unifying prefer_tuple and InferredListBinding — the purity pair was evaluated and rejected for cause — plus a decided inventory of every scattered confidence computation with a promotion rule (cross-boundary AND anchor-shaped), and the Trait.provenance format convention. Behavior byte-identical everywhere; single PR; independent of the other roadmap plans.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 All acceptance criteria in roadmap-plans.md Plan C are met, including: no existing test modified, the three-command gate green, the list-annotation predicate existing in exactly one src location, override tests proving both consumers route through the registry, and the documented inventory verdicts.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Execute roadmap-plans.md Plan C (normative, incl. its sequencing adjustments) via a dynamic workflow: impl-1 sonnet (oracle-first proof tests, then type-annotation provider + barrel wiring), impl-2 opus (cut both consumers over, confidence inventory + provenance convention + docs), haiku gate with fix loop, 3 opus lenses from the plan (behavior-identity, trait-contract/registry, boundary/barrel/self-lint), opus fixer, final gate. Orchestrator verifies, commits, PRs, merges.
<!-- SECTION:PLAN:END -->
