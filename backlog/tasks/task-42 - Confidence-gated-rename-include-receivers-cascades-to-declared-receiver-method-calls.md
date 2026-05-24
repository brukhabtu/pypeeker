---
id: TASK-42
title: >-
  Confidence-gated rename: --include-receivers cascades to declared-receiver
  method calls
status: Done
assignee:
  - '@claude'
created_date: '2026-05-24 04:10'
updated_date: '2026-05-24 04:11'
labels:
  - refactor
  - analysis
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Final DRAFT-2 item. plan-rename currently updates the definition, same-module/self references, and import statements, but not obj.method() call sites resolved via receivers. This adds an opt-in --include-receivers that additionally renames attribute/method call sites resolving to the target, but ONLY through high-confidence receivers: DECLARED type annotations, self/cls, and module/class receivers. Constructor-INFERRED receiver types are excluded, because rename mutates code and inference is best-effort. Implemented via a declared_only mode on the resolver (resolve_reference/find_all_references); the planner adds the is_attribute_access usages, and the existing text guard keeps only old_name tokens.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 resolver gains a declared_only mode: receiver steps relying on constructor-inferred types are not followed; DECLARED annotations, self/cls, and module/class receivers still resolve
- [x] #2 plan-rename --include-receivers renames method/attribute call sites resolved through declared/self/module/class receivers; default (no flag) behavior is unchanged
- [x] #3 Constructor-inferred receiver call sites (x = Foo(); x.m()) are NOT renamed even with --include-receivers
- [x] #4 End-to-end: renaming a method with --include-receivers updates self.m() and obj.m() (obj: Declared) producing runnable code; full suite green; pypeeker check exits 0
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
resolve.py: declared_only param on _container_of/_resolve_attr/resolve_reference/find_all_references (skip INFERRED type deref). engine.find_all_references declared_only passthrough. planner.plan(include_receivers): add is_attribute_access refs from find_all_references(declared_only=True). cli --include-receivers. Tests: declared receiver method renamed w/ flag, not w/o; inferred receiver NOT renamed w/ flag; e2e runnable. suite+check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
resolve.py: added declared_only mode threaded through resolve_reference/_resolve_attr/_container_of/find_all_references. In _container_of, the typed-receiver branch is skipped when declared_only and the type_annotation confidence is not DECLARED (i.e. constructor-INFERRED types are not followed). self/cls and module/class receivers are structural and always followed. engine.find_all_references gained the declared_only passthrough.

planner.plan(include_receivers=True): after the existing binding-id collection, adds is_attribute_access references from find_all_references(symbol, declared_only=True). Dedup + the existing actual_text==old_name guard keep edits correct. cli: --include-receivers flag.

Verified end-to-end: plan-rename lib:Svc.run -> execute --include-receivers rewrites the def and the declared-receiver call s.execute() (runnable). Without the flag, the call site is untouched; a constructor-inferred receiver (s = Svc()) is NOT renamed even with the flag.

Tests: 435 pass (default no-touch, declared receiver renamed w/ flag, inferred receiver excluded). pypeeker check exits 0. plan-rename default behavior unchanged.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Add confidence-gated rename via plan-rename --include-receivers (final DRAFT-2 item). By default rename stays on its exact-binding rule; with the flag it additionally renames method/attribute call sites that resolve to the target through a receiver, but only HIGH-CONFIDENCE ones: DECLARED type annotations, self/cls, and module/class receivers. Constructor-INFERRED receiver types are deliberately excluded, because rename mutates code and inference is best-effort.

Implementation: a declared_only mode on the resolver (resolve_reference/find_all_references/_container_of) that does not follow receiver steps relying on inferred types; the planner adds the is_attribute_access usages under the flag, with the existing text guard ensuring only old_name tokens are edited.

User impact: renaming a method now optionally cascades to obj.method() call sites where obj has a declared type (or is self), producing runnable code end-to-end. Verified: lib:Svc.run -> execute --include-receivers updates the def and s.execute(); the same rename without the flag, and on a constructor-inferred receiver, leaves call sites untouched.

Tests: 435 pass; pypeeker check exits 0. This closes DRAFT-2.
<!-- SECTION:FINAL_SUMMARY:END -->
