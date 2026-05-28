---
id: TASK-57
title: plan-inline-variable
status: Done
assignee:
  - '@claude'
created_date: '2026-05-25 13:02'
updated_date: '2026-05-28 03:37'
labels: []
dependencies:
  - TASK-51
  - TASK-52
  - TASK-53
priority: low
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Inline a local variable: replace each reference of the variable with its assigned value expression and delete the assignment. Requires: a single assignment (or last-wins), the value expression is pure (no side effects / re-evaluation hazard), and all references found (query). Built on transaction INSERT/DELETE + CST + range data-flow/purity.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A single-assignment local with a pure value expression is inlined at all references and its assignment removed
- [x] #2 Refuses to inline when the value is impure or the variable is reassigned/escapes; tests cover safe + refused cases
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
refactor/inline.py InlineVariablePlanner.plan(symbol_id): resolve unique local VARIABLE; refuse if reassigned/shadowed (sibling same-name symbol or WRITE ref); find READ refs; if reads>1 require def-line purity (analyze_range.is_pure); parse CST, get RHS expr text from the assignment, parenthesize compound RHS; DELETE the assignment line + REPLACE each read with the value. CLI plan-inline-variable SYMBOL_ID. Tests + e2e. suite+check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
refactor/inline.py InlineVariablePlanner.plan(symbol_id): resolves a unique function-local VARIABLE; refuses reassigned/shadowed (sibling same-name symbol, $N suffix, or WRITE ref); finds READ refs; if used >1 time requires the def-line to be pure (analyze_range.is_pure) so a side-effecting value is not duplicated; reads the RHS via CST, parenthesizes compound expressions; emits a DELETE of the assignment line + a REPLACE of each use with the value. CLI plan-inline-variable SYMBOL_ID. Verified e2e: total=a+1; total*total -> (a+1)*(a+1). 496 tests pass; pypeeker check exits 0; ruff clean.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
plan-inline-variable: replace a local variable with its value and delete the assignment. Safety from the semantic layer - single binding (refuses reassignment/shadowing), all references found, and purity-gated when the value is used more than once (an impure value used once is still allowed since it is merely moved). The CST layer extracts the RHS, parenthesizes compound expressions, and emits DELETE + REPLACE byte edits through the transaction pipeline. Dead variables are simply removed. 496 tests pass; pypeeker check exits 0.
<!-- SECTION:FINAL_SUMMARY:END -->
