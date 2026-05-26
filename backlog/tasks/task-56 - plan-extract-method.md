---
id: TASK-56
title: plan-extract-method
status: Done
assignee:
  - '@claude'
created_date: '2026-05-25 13:02'
updated_date: '2026-05-26 14:12'
labels: []
dependencies:
  - TASK-51
  - TASK-52
  - TASK-53
priority: low
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Extract a statement range into a new function/method. Uses range data-flow for parameters (read-before-defined) and return values (defined-in-range-read-after), refuses when control flow escapes the range (break/continue/return), synthesizes the def + call on the CST, and emits a transaction.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Extracts a range into a new function with correct params/returns; replaces the range with a call
- [x] #2 Refuses on control-flow escape or name conflicts; tests cover a clean extract end-to-end
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
refactor/extract.py ExtractMethodPlanner.plan(file,start,end,name): uses analyze_range for params (inputs) and returns (outputs); refuses ranges with control-flow escape (has_escape) and non-top-level functions (v1); synthesizes "def name(params): <dedented+reindented body> return outputs" inserted before the enclosing function, and replaces the range with "outputs = name(params)". CLI plan-extract-method FILE START_LINE END_LINE NAME (0-indexed inclusive). Made dataflow.enclosing_function_scope public. Discovered+fixed the id(node) reference bug (TASK-55) which this depended on. Verified end-to-end: extract lines 2-3 -> compute(a,b,d) returning e, valid Python. 488 tests pass; pypeeker check exits 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
plan-extract-method: extract a statement range into a new top-level function. The first refactor that consumes the semantic moat - analyze_range supplies parameters (names read in the range but defined outside) and return values (names produced in the range and read after), refuses ranges containing return/break/continue, and the CST layer synthesizes the def + call as byte edits through the transaction pipeline (formatting preserved). v1 scope: top-level functions, no control-flow escapes. Verified end-to-end. 488 tests pass; pypeeker check exits 0.
<!-- SECTION:FINAL_SUMMARY:END -->
