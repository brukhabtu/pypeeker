---
id: TASK-7
title: Add function-scope isolation tests for facts and checks
status: Done
assignee: []
created_date: '2026-04-30 03:59'
updated_date: '2026-05-02 00:20'
labels: []
dependencies:
  - TASK-6
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The current test suite never validates that fact extractors and check_purity correctly scope to a single function. Every test file contains exactly one analyzable function, so a broken subtree filter would let side effects from other functions leak in unnoticed.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Add a fact-layer test: file with two top-level impure functions (e.g., one prints, the other writes to a global); analyze only one; assert the other's effects do NOT appear in the facts
- [x] #2 Add a fact-layer test: nested function inside an impure outer; analyzing the inner returns no facts from the outer's body
- [x] #3 Add a check-layer counterpart: check_purity on a pure function in a file containing impure neighbors returns PROBABLY_PURE
- [x] #4 Add a direct test for AnalysisContext.reads_by_line: source with reads in the analyzed function AND reads in a sibling function; only the analyzed function's reads appear in the dict
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added TestScopeIsolation and TestRedsByLineScope test classes covering: (1) sibling impurity in another top-level function does not leak into target (test_sibling_impurity_does_not_leak); (2) outer function's print() does not leak into inner's analysis; (3) inner function's print() does not leak into outer's analysis (this case revealed and motivated a fix to _scope_subtree, which previously descended through nested FUNCTION/LAMBDA scopes and leaked their refs); (4) check_purity on a pure function in a file containing an impure neighbor stays pure; (5) AnalysisContext.reads_by_line only contains lines from the target function. Bug fix in src/pypeeker/analysis/context.py::_scope_subtree: now stops at FUNCTION/LAMBDA boundaries (their bodies don't execute as part of the enclosing function call) while still descending through COMPREHENSION (which DOES execute inline).
<!-- SECTION:FINAL_SUMMARY:END -->
