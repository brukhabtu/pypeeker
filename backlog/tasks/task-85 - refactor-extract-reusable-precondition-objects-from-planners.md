---
id: TASK-85
title: 'refactor: extract reusable precondition objects from planners'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-06-11 18:27'
updated_date: '2026-06-11 18:54'
labels:
  - refactor
  - m3-planner
dependencies: []
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Rename/extract/inline planners encode preconditions as inline raises (single assignment, no escape, purity, name conflicts, staleness). Extract them into named, independently evaluable precondition objects so the composite planner can re-validate guarded intents at materialization time.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Preconditions are first-class objects evaluable against current state, returning pass/fail+reason
- [ ] #2 Existing planners consume them with identical error messages and behavior (suite green, no test edits beyond imports)
- [ ] #3 Each existing planner's precondition set is enumerable for reuse by the batch scheduler
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add src/pypeeker/refactor/preconditions.py: PreconditionResult (ok+reason), Precondition base with stable name + evaluate(), evaluate_in_order() driver, and concrete precondition classes for rename/extract-variable/extract-method/inline with reasons byte-identical to current error messages. Preconditions needing mid-plan values (resolved symbol, rdf, parsed tree, affected files) take them as constructor args; resolution-style preconditions cache their resolved value (e.g. .symbol) for the planner.
2. Refactor RenamePlanner, ExtractVariablePlanner, ExtractMethodPlanner, InlineVariablePlanner: each builds its ordered precondition set via a private generator (later preconditions constructed from earlier cached results), plan() drives it with evaluate_in_order and raises the existing exception type with the exact reason on first failure; a public preconditions(...) method exposes the enumerable set for the batch scheduler (TASK-88).
3. Add tests/test_preconditions.py: pass+fail per precondition in isolation, enumerability of each planner set, exact message identity with planner errors.
4. Run uv run pytest -q; existing planner/extract/inline tests must pass unmodified.
<!-- SECTION:PLAN:END -->
