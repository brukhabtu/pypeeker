---
id: TASK-9
title: Cover tricky Python constructs in purity tests
status: To Do
assignee: []
created_date: '2026-04-30 03:59'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Several common Python constructs aren't exercised by the test suite. Each is a place where the binder/analysis could behave unexpectedly. Tests should pin down expected behavior — even when current behavior is 'unknown' or 'over-flagged', that becomes documented.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Empty function ('def f(): pass') -> PROBABLY_PURE with empty evidence
- [ ] #2 Lambda expression assigned to a name -> document expected behavior (lambda has its own scope)
- [ ] #3 List/dict/set comprehension containing print() -> document expected behavior (comprehensions create their own scope in pypeeker's model)
- [ ] #4 Generator function with 'yield' -> document current behavior (no rule today, so PROBABLY_PURE; record this as the baseline so a future generator-detection rule has a regression target)
- [ ] #5 Function with a decorator: '@some_func\ndef f(): pass' -> verify decoration doesn't break symbol resolution
- [ ] #6 Class __init__ assigning multiple self attributes -> IMPURE with N attribute_write evidence items
- [ ] #7 Function calling another project-internal function (resolved call) -> currently PROBABLY_PURE; document this as the baseline for transitive purity work
- [ ] #8 Function with 'pass' body inside a class -> PROBABLY_PURE
- [ ] #9 Multiple impure effects in one function (3 print calls on different lines) -> 3 distinct evidence items with correct line numbers
<!-- AC:END -->
