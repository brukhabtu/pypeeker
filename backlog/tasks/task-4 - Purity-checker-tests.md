---
id: TASK-4
title: 'Purity checker: tests'
status: Done
assignee:
  - '@claude'
created_date: '2026-04-29 23:33'
updated_date: '2026-04-29 23:40'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Comprehensive tests using the existing indexed_project fixture. Cover pure functions, write-to-outer, global/nonlocal, impure builtin calls, impure stdlib calls, nested scopes, generators, and unknown cases.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Pure function (just reads params and returns) -> PROBABLY_PURE with empty evidence
- [x] #2 Function that calls print() -> IMPURE with calls_impure_builtin evidence
- [x] #3 Function that writes to a module-level variable -> IMPURE with writes_outer_scope evidence
- [x] #4 Function with 'global counter; counter += 1' -> IMPURE with global_declaration + writes_outer_scope evidence
- [x] #5 Nested function reading enclosing variable (read-only closure) -> PROBABLY_PURE
- [x] #6 Nested function writing enclosing variable via nonlocal -> IMPURE
- [x] #7 Function calling os.system() -> IMPURE with calls_impure_stdlib evidence
- [x] #8 Symbol not found -> PurityResult with UNKNOWN verdict and clear error evidence
- [x] #9 Non-function symbol (e.g., class) -> PurityResult with UNKNOWN verdict
- [x] #10 All existing 172 tests still pass
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Create tests/test_purity.py with helper to bind+save inline source (similar to existing patterns)\n2. Write test classes covering: pure functions, write to outer, attribute write (self.x = y), global+nonlocal redirect, impure builtins (print/open/input), impure stdlib (os.system, time.time, random.random), local list mutation suppression (must be pure), parameter mutation (must be impure), unknown symbol, non-function symbol\n3. Run full pytest suite to confirm 172 + new tests all pass
<!-- SECTION:PLAN:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added tests/test_purity.py with 18 tests across 7 test classes covering: pure functions (read-only, local assignment, local list mutation), impure builtin calls (print, open, input), impure stdlib calls (os.system, time.time, random.random), writes to outer scope (global, nonlocal redirect, closure read-only), attribute writes (self.attr = x), parameter mutation (lst.append() flagged because receiver is a parameter), edge cases (symbol not found, class instead of function, pure methods), and evidence metadata (line numbers, targets). 18/18 new tests pass; full suite 190/190 passes (was 172, added 18). Purity package coverage is 94%.
<!-- SECTION:FINAL_SUMMARY:END -->
