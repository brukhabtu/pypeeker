---
id: TASK-38
title: 'Binder: infer variable type from constructor assignment (query-only)'
status: Done
assignee:
  - '@claude'
created_date: '2026-05-24 02:36'
updated_date: '2026-05-24 02:38'
labels:
  - binder
  - analysis
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Gap A part 3 (first item, promoted from DRAFT-2). Resolve x = Foo(); x.method() without an annotation by inferring the variable type from the constructor call. The binder records an INFERRED TypeAnnotation on a variable when the RHS is a simple constructor call (Name(...) or dotted.Name(...)) and there is no explicit annotation; the existing instance-receiver resolver path (_resolve_attr) then resolves x.method via value -> type -> class -> member with no further change. Confidence is INFERRED so consumers can gate: queries (find_all_references, call graph) use it; plan-rename stays exact-binding. Over-recording is safe because resolution only succeeds when the recorded name resolves to a class that actually has the member.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 visit_assignment records TypeAnnotation(raw=<ctor name>, confidence=INFERRED) on a variable when there is no explicit annotation, the RHS is a call to a simple/dotted name, and there is a single LHS target
- [x] #2 Tuple unpacking, chained/awaited/subscripted RHS, and explicitly-annotated assignments are unaffected (no inference, or DECLARED kept)
- [x] #3 find_all_references and the call graph resolve x = Foo(); x.method() to Foo.method (query-only); plan-rename behavior is unchanged
- [x] #4 Purity impact is verified: constructor-inferred types may feed typed-receiver dispatch; the purity suite passes (decision: INFERRED types are accepted as more precise)
- [x] #5 Full suite green; pypeeker check exits 0
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
assignments.py: add _constructor_type_name(right) -> name for call RHS with identifier/attribute function; in visit_assignment, when type_ann is None and single target and RHS is a constructor call, set type_ann INFERRED. Resolver path already handles it. Tests: x=Foo();x.m() resolves in find_all_references + call graph; tuple-unpack/non-call unaffected; INFERRED confidence set. Verify purity suite. suite+check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
assignments.py: added _constructor_type_name(right) returning the call target name for identifier/attribute-function calls; visit_assignment records TypeAnnotation(raw=name, confidence=INFERRED) on a single-target assignment with no explicit annotation and a constructor-call RHS. The instance-receiver resolver path (_resolve_attr, TASK-37) handles resolution unchanged.

Decision #1 resolved: INFERRED types are accepted into purity typed-receiver dispatch; full purity suite passes unchanged. plan-rename does not use resolve_reference, so it is unaffected (query-only).

Dogfood: zero-ref method candidates 86 -> 81 (cumulative 94 -> 81 across parts 2-3). Remaining are CLI Click callbacks, test-only public API, and multi-hop self.attr chains (still in DRAFT-2).

Tests: 426 pass (x=Foo();x.run() resolves in find_all_references + call graph; INFERRED confidence set; tuple-unpack and non-call RHS not inferred). pypeeker check exits 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Infer a variable type from a constructor assignment so x = Foo(); x.method() resolves without an annotation (Gap A, part 3 / query-only). The binder records an INFERRED TypeAnnotation on a single-target variable whose RHS is a simple constructor call (Name(...) / dotted.Name(...)); the existing instance-receiver resolver (value -> type -> class -> member) then resolves the access with no further change. Over-recording is safe: resolution only succeeds when the recorded name is a class that has the member.

Confidence gates consumers: queries (find_all_references, call graph) use INFERRED types; plan-rename stays on its exact-binding rule (unchanged). Purity now also sees constructor-inferred receiver types (accepted as more precise); the purity suite passes unchanged.

User impact: instance-method usages via constructor-assigned locals are now discoverable. On pypeeker, zero-reference method candidates fell 86 -> 81 (94 -> 81 across the instance-receiver work). Remaining residual is entry points, test-only API, and multi-hop self.attr chains.

Tests: 426 pass; pypeeker check exits 0. Multi-hop chains and confidence-gated rename remain in DRAFT-2.
<!-- SECTION:FINAL_SUMMARY:END -->
