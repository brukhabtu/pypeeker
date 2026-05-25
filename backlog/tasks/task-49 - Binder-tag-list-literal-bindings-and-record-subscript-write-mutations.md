---
id: TASK-49
title: 'Binder: tag list-literal bindings and record subscript-write mutations'
status: Done
assignee:
  - '@claude'
created_date: '2026-05-25 12:44'
updated_date: '2026-05-25 12:45'
labels:
  - binder
  - analysis
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Primitives for a future prefer-tuple analysis (and generally useful). Two gaps: (1) x = [...] records no type, so list-literal bindings are invisible; (2) x[i] = v records x as a READ, so subscript mutations are invisible. Fix: infer type list (INFERRED) for list-literal/comprehension RHS on a single target (like constructor inference), and record the root of a subscript assignment target (x[i] = v, x[i] += v) as a WRITE reference so mutations through indexing are tracked.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 x = [...] and x = [c for c in ...] tag the variable with an INFERRED type of list (single target, no explicit annotation)
- [x] #2 x[i] = v and x[i] += v record the subscript root x as a WRITE reference (mutation), not just a read; nested x[i][j] = v resolves to root x
- [x] #3 Existing behavior is unchanged except the new signals; full suite green; pypeeker check exits 0
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
assignments.py: _literal_list_type, _subscript_root_identifier, _record_subscript_mutation. visit_assignment: extend type inference with list literal; record subscript-target mutation. visit_augmented_assignment: record subscript-target mutation. Tests in test_binder. suite+check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
assignments.py: _literal_list_type tags list/list-comprehension RHS as INFERRED type "list" (single target); _subscript_root_identifier + _record_subscript_mutation record x[i]=v / x[i]+=v / x[i][j]=v as a WRITE of root x. Verified a/b/c/d tagged list, c[0]=9 is a WRITE. 449+6 tests pass; pypeeker check exits 0; no purity behavior change observed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Two binder primitives for collection-mutation analysis: list-literal bindings (x = [...] / comprehensions) are tagged with an INFERRED "list" type, and subscript assignment targets (x[i] = v, x[i] += v, nested x[i][j] = v) record the root as a WRITE reference so mutations through indexing are tracked (previously recorded as reads). Foundations for a prefer-tuple rule. 455 tests pass; pypeeker check exits 0.
<!-- SECTION:FINAL_SUMMARY:END -->
