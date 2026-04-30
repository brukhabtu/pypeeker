---
id: TASK-7
title: Add function-scope isolation tests for facts and checks
status: To Do
assignee: []
created_date: '2026-04-30 03:59'
updated_date: '2026-04-30 04:03'
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
- [ ] #1 Add a fact-layer test: file with two top-level impure functions (e.g., one prints, the other writes to a global); analyze only one; assert the other's effects do NOT appear in the facts
- [ ] #2 Add a fact-layer test: nested function inside an impure outer; analyzing the inner returns no facts from the outer's body
- [ ] #3 Add a check-layer counterpart: check_purity on a pure function in a file containing impure neighbors returns PROBABLY_PURE
- [ ] #4 Add a direct test for AnalysisContext.reads_by_line: source with reads in the analyzed function AND reads in a sibling function; only the analyzed function's reads appear in the dict
<!-- AC:END -->
