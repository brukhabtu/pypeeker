---
id: TASK-44
title: >-
  Resolver: dereference callable return types to improve receiver resolution
  reliability
status: Done
assignee:
  - '@claude'
created_date: '2026-05-24 12:07'
updated_date: '2026-05-24 12:08'
labels:
  - analysis
  - index
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Reliability improvement for the call graph / find_all_references / dead-code analysis. The seeded dead-code pass left false positives because the resolver could not follow two common patterns: a receiver that is a property/method whose return type is the real container (self.current.lookup_local() where current -> ScopeEntry), and a variable assigned from a function call (result = index_path(); result.to_dict() where index_path -> IndexResult). Both have the needed data already: functions/methods/properties store their return type in Symbol.type_annotation. This generalizes _container_of so that when a receiver resolves to a callable, its return type is dereferenced to the next container (bounded recursion), and routes all type-name->class resolution through one helper. Query-only; confidence gating still applies (constructor-inferred variable types remain excluded from rename via declared_only).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 _container_of follows a callable receivers return-type annotation to its class: self.prop.method() and obj.method() via a property/method-typed member resolve
- [x] #2 A variable assigned from a function call resolves through the functions return type: result = f(); result.m() resolves when f has a return annotation (bounded recursion guards return-type cycles)
- [x] #3 find_all_references and the call graph gain these edges; previously-false-positive dead-code candidates (lookup_local, to_dict) now resolve; plan-rename declared_only still excludes constructor-inferred variable receivers
- [x] #4 Full suite green; pypeeker check exits 0
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
resolve.py: add _class_from_type_name(raw, owner_id, declared_only, depth) that resolves a type name in owner module and, if it lands on a function/method, follows its return type (bounded). Rewrite _container_of: module/class -> self; callable -> return-type via helper; typed param/var (confidence-gated) -> helper; self/cls -> enclosing class. Tests: property-chain, function-return variable, cycle guard. Re-probe dead-code FPs. suite+check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
resolve.py: _container_of now dereferences callable receivers (function/method/@property) to the class of their return type, and routes all type-name->class resolution through a new _class_from_type_name helper that follows a callables return type (bounded by _MAX_TYPE_HOPS=3 to terminate on self-referential return types). Removed the now-orphaned _is_class helper.

Reliability: two former dead-code false positives now resolve - ScopeEntry.lookup_local 0->1 (via self.current.lookup_local() property chain) and IndexResult.to_dict 0->1 (via result = index_path(); result.to_dict() function-return). Confidence gating preserved: the constructor-inferred variable step is still excluded under declared_only, so rename is unaffected.

Tests: 438 pass (property chain, function-result variable, return-type cycle terminates). pypeeker check exits 0.

Remaining reliability frontiers (future): instance attributes assigned in __init__ without annotation (self._engine = Foo()) need instance-attribute symbols + type inference; dynamic call-receivers (self._get_resolver().x()) need the binder to keep call results in the receiver chain.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Improve receiver-resolution reliability by dereferencing callable return types. The seeded dead-code pass left false positives where the real container was the return type of a property/method (self.current.lookup_local() -> ScopeEntry) or of a function whose result was stored in a variable (result = index_path(); result.to_dict() -> IndexResult). Functions/methods/properties already store their return type in Symbol.type_annotation, so _container_of now follows it.

_container_of resolves, in order: module/class -> itself; callable -> the class of its return type; typed param/var -> its types class; self/cls -> enclosing class. All type-name->class resolution goes through a new _class_from_type_name helper that follows callable return types with bounded recursion (terminates on self-referential return types). The orphaned _is_class helper was removed.

Query-only; confidence gating intact (constructor-inferred variable types remain excluded from rename via declared_only). Reliability win: ScopeEntry.lookup_local and IndexResult.to_dict (former dead-code FPs) now resolve. 438 tests pass; pypeeker check exits 0.

Follow-ups for further reliability: __init__-assigned instance attributes (self._engine = Foo()) and dynamic call-receiver chains (self._get_resolver().x()).
<!-- SECTION:FINAL_SUMMARY:END -->
