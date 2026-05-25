---
id: TASK-50
title: 'check: prefer-tuple advisory rule'
status: Done
assignee:
  - '@claude'
created_date: '2026-05-25 12:47'
updated_date: '2026-05-25 12:49'
labels:
  - check
  - analysis
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
An opt-in check rule (built on TASK-49 primitives) that flags a function-local variable bound to a list literal that is never mutated (no subscript write, no list-mutating method call like append/extend/sort) and suggests a tuple. Advisory and best-effort: a list passed to a function that mutates it, or aliased and mutated via the alias, cannot be detected without escape analysis - documented as a known limitation. Not enabled by default; consumers opt in via [tool.pypeeker].rules. Built-in (registered) so it is available out of the box.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 prefer-tuple flags a function-local list-literal variable (INFERRED list type) with no mutation: no WRITE reference and no list-mutating method call (append/extend/insert/remove/pop/clear/sort/reverse) on it
- [x] #2 A list mutated via subscript (x[i]=v) or a mutating method is NOT flagged; module/class-level lists are out of scope (cross-file mutation invisible)
- [x] #3 The rule is registered and available but NOT in pypeeker default rules; the escape/aliasing limitation is documented
- [x] #4 Tests cover: unmutated local list flagged; append-mutated and subscript-mutated lists not flagged; full suite green; pypeeker check exits 0
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
check/rules.py: PREFER_TUPLE rule. candidates = VARIABLE symbols, type_annotation raw==list & INFERRED, parent scope kind FUNCTION/LAMBDA. mutated if any WRITE ref to it or any CALL attr ref with receiver_root==it and leaf in list-mutators. flag unmutated. Register in REGISTRY (not default). Tests in test_check_rules. suite+check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
check/rules.py: prefer_tuple flags function-local VARIABLE symbols with INFERRED list type that are never mutated (no WRITE ref = no subscript write; no list-mutating method append/extend/insert/remove/pop/clear/sort/reverse on the var as receiver). Module/class-level lists skipped (cross-file mutation invisible). Registered in REGISTRY but NOT added to pypeeker default rules. Dogfood on src: ~8+ candidates, all technically never-mutated locals incl. comprehension temporaries; value varies and some involve escape (passed to functions) the rule cannot see - confirming the advisory/opt-in design. 462 tests pass; default pypeeker check still exits 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Add an opt-in prefer-tuple check rule: flags a function-local variable bound to a list literal that is never mutated (no subscript write, no list-mutating method call) and suggests a tuple. Built on the TASK-49 binder primitives (list-literal tagging + subscript-write mutations). Advisory and best-effort - it cannot see mutation through escape (passing the list to a function) or aliasing, so it is registered but not enabled by default; consumers opt in via [tool.pypeeker].rules. Module/class-level lists are out of scope for a per-file rule. 462 tests pass; pypeeker check exits 0.
<!-- SECTION:FINAL_SUMMARY:END -->
