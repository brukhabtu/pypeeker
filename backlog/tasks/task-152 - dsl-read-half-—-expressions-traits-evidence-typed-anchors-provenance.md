---
id: TASK-152
title: 'dsl: read half — expressions, traits, evidence-typed anchors, provenance'
status: Done
assignee:
  - '@claude'
created_date: '2026-08-03 18:10'
updated_date: '2026-08-04 02:17'
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
- [x] #1 The dsl package expresses selections over all five universes with derived scope and written-order evaluation
- [x] #2 Composed traits register into the existing registry; confidence meets over every contribution; the DECLARED meta-read law holds and is tested against the prefer-tuple shape
- [x] #3 Unresolved and ambiguous anchor lookups produce structured errors with candidates, never empty results
- [x] #4 --why returns the derivation tree as versioned JSON
- [x] #5 Full gate green; differential harness still green (no rules claimed yet)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v4 in /home/user/pypeeker-wt152. Forks in dsl-rewrite.md binding; surface spelling is the pipeline's within them. Orchestrator ships, then runs the first-parity-claim milestone before fanning out phase 3.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Pipeline run wf_0b24049d-e11 (19/19 agents, ~2.2M subagent tokens). Two adversarial rounds; all must-fix findings fixed: AllOf/Opaque evidence laundering (short-circuit is now a context-granted licence, withdrawn wherever falsity is consumed — order-independent confidence proven over permutations), silent-[] on opaque reads of projected-away fields (now a loud UnknownFieldError naming the opaque).
- Orchestrator closeout of the two non-blocking findings: appended a divergence-ledger entry for the binder typed-variadic fix (shifts frozen oracle output; both engines read the same binder so the harness is structurally blind to it); hyphenated ReachError/AnchorError codes (reach-refused, anchor-error) and moved install_expressions() inside the query command refusal envelope.
- 415 new tests (3,071 total); independent four-step gate PASS in the worktree.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Landed phase 2 of the DSL rewrite: the read half, as the new src/pypeeker/dsl/ package plus a `pypeeker query` CLI command.

Changes:
- Selections over the five universes (symbols, references, imports, modules, scopes) with where/follow/project, written-order evaluation, no optimizer; reach (file vs project) derived from the expression, never declared.
- Evidence lattice: confidence meets over every contribution on DECLARED > INFERRED > HEURISTIC with UNKNOWN absorbing; order-independent by construction — AllOf short-circuits only under a context that discards falsity, so a negated conjunction still meets over every clause. The .at(level) meta-read reports DECLARED, proven end-to-end by the prefer-tuple shape (the ledger constraint).
- where() rejects bare callables; opaque(name, reads=...) is the escape hatch; a declared read of a projected-away universe field refuses loudly instead of returning [].
- trait(name, expr) registers composed traits into the existing analysis registry (no second table), refusing PROJECT-reach expressions, non-symbol-universe fields, and accidental shadowing of providers it did not create.
- Evidence-typed anchors: unresolved/ambiguous lookups are structured errors with candidates; a CLI-typed id is DECLARED evidence.
- --why: versioned additive-only JSON derivation trees ({schema: 1, derivations: [...]}), provenance never serialized.
- Substrate fix with ledger entry: binder now binds typed variadic parameters (*args: T / **kwargs: T); recorded in dsl-rewrite.md since it shifts the frozen oracle's observable output.

Tests: 415 new (3,071 total); four-step gate (pytest, ruff, self-lint zero findings, differential with empty manifest) PASS.

Follow-ups: phase 3 rule ports (TASK-153/154/155) claim rules in the parity manifest and are graded differentially.
<!-- SECTION:FINAL_SUMMARY:END -->
