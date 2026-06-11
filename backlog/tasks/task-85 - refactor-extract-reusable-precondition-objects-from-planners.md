---
id: TASK-85
title: 'refactor: extract reusable precondition objects from planners'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 18:27'
updated_date: '2026-06-11 19:04'
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
- [x] #1 Preconditions are first-class objects evaluable against current state, returning pass/fail+reason
- [x] #2 Existing planners consume them with identical error messages and behavior (suite green, no test edits beyond imports)
- [x] #3 Each existing planner's precondition set is enumerable for reuse by the batch scheduler
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add src/pypeeker/refactor/preconditions.py: PreconditionResult (ok+reason), Precondition base with stable name + evaluate(), evaluate_in_order() driver, and concrete precondition classes for rename/extract-variable/extract-method/inline with reasons byte-identical to current error messages. Preconditions needing mid-plan values (resolved symbol, rdf, parsed tree, affected files) take them as constructor args; resolution-style preconditions cache their resolved value (e.g. .symbol) for the planner.
2. Refactor RenamePlanner, ExtractVariablePlanner, ExtractMethodPlanner, InlineVariablePlanner: each builds its ordered precondition set via a private generator (later preconditions constructed from earlier cached results), plan() drives it with evaluate_in_order and raises the existing exception type with the exact reason on first failure; a public preconditions(...) method exposes the enumerable set for the batch scheduler (TASK-88).
3. Add tests/test_preconditions.py: pass+fail per precondition in isolation, enumerability of each planner set, exact message identity with planner errors.
4. Run uv run pytest -q; existing planner/extract/inline tests must pass unmodified.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Added src/pypeeker/refactor/preconditions.py: PreconditionResult, Precondition base (stable name + evaluate()), evaluate_in_order() driver, and 18 concrete preconditions covering rename/extract-variable/extract-method/inline with reasons byte-identical to historical messages.
- Refactored planner.py, extract.py, inline.py onto the precondition sets via per-planner _iter_preconditions generators + public preconditions() enumerators; plan() raises the existing exception types with identical messages.
- Existing test_planner/test_extract_*/test_inline_variable/test_rename_cli pass unmodified; ruff clean on refactor/.

- Added tests/test_preconditions.py (53 tests): pass+fail per precondition in isolation, generator-driver semantics, enumerability (with truncation-at-failure) of all four planner sets, and exact message identity between plan() exceptions and precondition reasons.
- Full suite: uv run pytest -q -> 888 passed, 0 failures. Existing test files unmodified (verified via git diff).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Extracted the rename/extract/inline planners' inline-raise validations into first-class, reusable precondition objects, laying the foundation for the composite batch planner (TASK-88) to re-validate guarded intents at materialization time.

Changes:
- New src/pypeeker/refactor/preconditions.py: PreconditionResult (ok+reason), Precondition base class (stable name + evaluate()), evaluate_in_order() driver that stops at the first failure and supports generators whose later preconditions are built from earlier cached results, and 18 concrete preconditions. Reasons are byte-identical to the historical planner error messages. Preconditions needing mid-plan values (resolved symbol, range dataflow, parsed CST, computed affected-file set) take them as constructor args; resolution-style preconditions cache what they resolved (.symbol, .dataflow, .node, .index, .rhs, .func_scope) for reuse — both patterns documented in the module docstring.
- Inventory: shared {valid-identifier, file-exists, file-fresh}; rename {rename-flags-compatible, symbol-resolves-uniquely, new-name-differs, no-scope-name-conflict, affected-files-fresh}; extract-variable {expression-found, inside-statement}; extract-method {range-inside-function, no-control-flow-escape, top-level-function-only}; inline {local-variable-resolves, loaded-index-fresh, not-reassigned, multi-use-value-pure, assignment-locatable}.
- planner.py / extract.py / inline.py: each planner builds its ordered set via a private _iter_preconditions generator (intermediates stashed on a per-planner state dataclass), plan() drives it and raises the existing exception type with the identical message on first failure, and a public preconditions(...) method exposes the enumerable set (truncating at the first failing precondition) for the batch scheduler.

Tests:
- New tests/test_preconditions.py (53 tests): pass+fail per precondition in isolation, driver semantics, enumerability of all four planner sets, exact message identity with planner errors.
- uv run pytest -q: 888 passed, 0 failures; existing planner/extract/inline tests pass unmodified. ruff clean.

Risks/follow-ups: CST-backed preconditions bind a source snapshot at construction; callers wanting a fully current re-check should rebuild the set via planner.preconditions(...) (documented). TASK-88 consumes this.
<!-- SECTION:FINAL_SUMMARY:END -->
