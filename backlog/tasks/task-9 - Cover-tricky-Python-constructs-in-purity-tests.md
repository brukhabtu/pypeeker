---
id: TASK-9
title: Cover tricky Python constructs in purity tests
status: Done
assignee: []
created_date: '2026-04-30 03:59'
updated_date: '2026-05-02 00:20'
labels: []
dependencies:
  - TASK-10
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Several common Python constructs aren't exercised by the test suite. Each is a place where the binder/analysis could behave unexpectedly. Tests should pin down expected behavior — even when current behavior is 'unknown' or 'over-flagged', that becomes documented.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Empty function ('def f(): pass') -> PROBABLY_PURE with empty evidence
- [x] #2 Lambda expression assigned to a name -> document expected behavior (lambda has its own scope)
- [x] #3 List/dict/set comprehension containing print() -> document expected behavior (comprehensions create their own scope in pypeeker's model)
- [x] #4 Generator function with 'yield' -> document current behavior (no rule today, so PROBABLY_PURE; record this as the baseline so a future generator-detection rule has a regression target)
- [x] #5 Function with a decorator: '@some_func\ndef f(): pass' -> verify decoration doesn't break symbol resolution
- [x] #6 Class __init__ assigning multiple self attributes -> IMPURE with N attribute_write evidence items
- [x] #7 Function calling another project-internal function (resolved call) -> currently PROBABLY_PURE; document this as the baseline for transitive purity work
- [x] #8 Function with 'pass' body inside a class -> PROBABLY_PURE
- [x] #9 Multiple impure effects in one function (3 print calls on different lines) -> 3 distinct evidence items with correct line numbers
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added TestTrickyConstructs class plus standalone tests covering: empty function (def f(): pass) -> PROBABLY_PURE; class method with pass body -> PROBABLY_PURE; project-internal call (resolved CALL ref to local helper) -> currently PROBABLY_PURE locally (transitive analysis in TASK-15 handles propagation); class __init__ with self.attr = x -> IMPURE with one ATTRIBUTE_WRITE evidence (binder limitation: only emits ref for the first sequential self.x = y assignment, documented as a follow-up regression target); decorated function -> resolves normally without breaking purity check; generator (yield) -> currently PROBABLY_PURE as documented baseline; lambda body does not leak into outer function (scope-isolation test); comprehension with print -> IMPURE (comprehensions execute inline, unlike nested defs). Multi-effect test in TestEvidenceMetadata asserts a single function with three impure operations (global write + print + os.system) produces exactly 3 evidence items with correct kinds and lines in the function's body range.
<!-- SECTION:FINAL_SUMMARY:END -->
