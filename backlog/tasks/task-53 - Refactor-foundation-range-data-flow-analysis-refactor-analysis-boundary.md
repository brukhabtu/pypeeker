---
id: TASK-53
title: 'Refactor foundation: range data-flow analysis + refactor->analysis boundary'
status: Done
assignee:
  - '@claude'
created_date: '2026-05-25 13:01'
updated_date: '2026-05-26 12:33'
labels:
  - refactor
  - analysis
  - foundation
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Reuse the analysis layer (AnalysisContext: scope subtree, local symbols, reads/writes) to answer the data-flow questions complex refactors need for a statement range inside a function: inputs (names read in the range but defined outside it), outputs (names defined in the range and read after it), whether the range has control-flow escapes (break/continue/return) and whether it is side-effect-free (purity). Add refactor->analysis to the import-boundaries allow-list (a sound downward edge).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A range data-flow helper returns inputs and outputs for a statement range within a function, built on AnalysisContext
- [x] #2 It reports control-flow escapes (break/continue/return crossing the range boundary) and purity (side-effect-free) for the range
- [x] #3 import-boundaries allow-list adds refactor -> analysis; pypeeker check exits 0
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
purity.py: expose observations(ctx). refactor/dataflow.py: RangeDataFlow(inputs,outputs,has_escape,is_pure); analyze_range(store,file,start,end): find enclosing FUNCTION scope, build AnalysisContext; inputs=locals read in range defined outside; outputs=locals written in range read after; has_escape=return/break/continue node in range (CST); is_pure=no observation lines in range. pyproject: refactor->analysis. Tests test_refactor_dataflow.py. suite+check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
refactor/dataflow.py: analyze_range(store,file,start,end) -> RangeDataFlow(inputs, outputs, has_escape, is_pure). Finds the innermost FUNCTION scope containing the range, builds AnalysisContext. inputs = locals read in range whose declaration is outside the range (params + vars defined before). outputs = locals produced in range (declared-in-range, since a plain assignment is a declaration not a WRITE ref, plus re-assignment WRITE refs) and read after the range. has_escape = return/break/continue node in range (CST walk). is_pure = no purity observation lines fall in the range (added public observations(ctx) to purity). import-boundaries: refactor -> analysis added. 481 tests pass; pypeeker check exits 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Range data-flow analysis for structural refactors (refactor/dataflow.py). For a line range inside a function it computes inputs (locals read in the range but defined outside -> parameters), outputs (locals produced in the range and read afterward -> return values), control-flow escape (return/break/continue in the range, via CST), and purity (no impure observations in the range). Built on AnalysisContext + a new public purity.observations() for data flow and the CST for control-flow shape. Adds the refactor -> analysis import boundary. This is the safety/data-flow input for extract-method and friends. 481 tests pass; pypeeker check exits 0.
<!-- SECTION:FINAL_SUMMARY:END -->
