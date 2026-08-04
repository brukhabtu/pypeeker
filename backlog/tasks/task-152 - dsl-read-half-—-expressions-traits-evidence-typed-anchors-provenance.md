---
id: TASK-152
title: 'dsl: read half — expressions, traits, evidence-typed anchors, provenance'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-08-03 18:10'
updated_date: '2026-08-03 23:40'
labels: []
dependencies:
  - TASK-151
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 2 of the DSL rewrite (dsl-rewrite.md is normative — read it first, including every fork resolution; they are settled). One pipeline arc delivering the dsl/ package: five universes (symbols, references, imports, modules, scopes); where/follow/project with the predicate grammar; written-order evaluation with NO optimizer; the evidence lattice with meet-over-every-contribution and the stated DECLARED-meta-read law; where() rejecting bare callables with the named reads=-declaring escape; scope derived from the expression; trait(name, expr) registering into the existing analysis/traits.py registry; evidence-typed anchor resolution where unresolved or ambiguous lookups are loud structured errors; versioned additive-only --why provenance. No mutation terminals in this phase.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The dsl package expresses selections over all five universes with derived scope and written-order evaluation
- [ ] #2 Composed traits register into the existing registry; confidence meets over every contribution; the DECLARED meta-read law holds and is tested against the prefer-tuple shape
- [ ] #3 Unresolved and ambiguous anchor lookups produce structured errors with candidates, never empty results
- [ ] #4 --why returns the derivation tree as versioned JSON
- [ ] #5 Full gate green; differential harness still green (no rules claimed yet)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v4 in /home/user/pypeeker-wt152. Forks in dsl-rewrite.md binding; surface spelling is the pipeline's within them. Orchestrator ships, then runs the first-parity-claim milestone before fanning out phase 3.
<!-- SECTION:PLAN:END -->
