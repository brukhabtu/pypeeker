---
id: TASK-10
title: Strengthen evidence-shape assertions across purity tests
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
Most existing tests only check verdict + evidence-kind membership. This lets several classes of regression slip through silently: doubled evidence, wrong line numbers, wrong confidence, accidental false-positive evidence on pure cases.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Every test asserting PROBABLY_PURE also asserts result.evidence == [] (currently only one test does)
- [x] #2 Every test asserting IMPURE also asserts an exact evidence count (e.g., 'assert len(result.evidence) == 1' or '== 3')
- [x] #3 Every impure-call test asserts the line number of the evidence (not just kind+target)
- [x] #4 Add at least one test that asserts result.confidence == Confidence.HEURISTIC (currently no test reads this field)
- [x] #5 Add a 'multiple effects in one function' test: function with print + os.system + global write -> assert evidence list contains exactly 3 items, with correct kinds, lines, and targets, in source order
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Strengthened assertions across tests/test_purity.py: (1) every PROBABLY_PURE test now uses _assert_pure() helper that asserts verdict == PROBABLY_PURE AND result.evidence == [] AND result.confidence == HEURISTIC; (2) every single-effect IMPURE test asserts exact evidence count (assert len(result.evidence) == 1) — catches double-flag regressions; (3) every impure-call test asserts exact line numbers (e.g. ev.line == 1 for 'def f():\n    print(x)\n'); (4) confidence == HEURISTIC asserted in _assert_pure (every pure test) plus explicitly in test_print_call_is_impure; (5) test_multiple_effects_produce_multiple_evidence verifies that a single function with three impure operations produces exactly 3 evidence items with the expected three EvidenceKinds (WRITES_OUTER_SCOPE, CALLS_IMPURE_BUILTIN, CALLS_IMPURE_MODULE) and lines in the function's body range.
<!-- SECTION:FINAL_SUMMARY:END -->
