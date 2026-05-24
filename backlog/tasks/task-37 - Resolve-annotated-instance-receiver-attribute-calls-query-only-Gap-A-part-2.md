---
id: TASK-37
title: >-
  Resolve annotated instance-receiver attribute calls (query-only) (Gap A, part
  2)
status: Done
assignee:
  - '@claude'
created_date: '2026-05-24 02:07'
updated_date: '2026-05-24 02:09'
labels:
  - analysis
  - index
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Gap A, part 2. Part 1 resolved module- and class-qualified attribute access; this resolves instance receivers that carry a declared type annotation: for store.save() where store: IndexStore, resolve via the receiver value -> its declared type -> the class -> the member. Reuses the bare-type-name normalizer (moved from analysis/context.py to resolve.py for single-source) and the existing _members map, so it is a small extension of resolve_reference. QUERY-ONLY: this powers find_all_references and the call graph only; plan-rename stays on its exact-binding rule and is intentionally NOT changed (annotation-based receiver inference is best-effort, not sound enough to mutate code on). Constructor-assignment inference (x = Foo()), multi-hop chains (a.b.c), and confidence-gated rename integration are deferred (filed as a follow-up).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 resolve_reference resolves a single-hop receiver.attr where the receiver root is a PARAMETER/VARIABLE with a declared type annotation: the bare type name is resolved (in the receiver module) to a class and the member named attr is returned (through re-exports)
- [x] #2 _bare_type_name is moved to resolve.py as bare_type_name (single source) and analysis/context.py imports it; behavior unchanged
- [x] #3 find_all_references and the call graph now include annotated instance-method usages (e.g. store.save() where store: IndexStore); receivers without a usable annotation, builtins, and external types resolve to nothing without error
- [x] #4 plan-rename is unchanged (query-only); a follow-up task captures constructor inference, multi-hop chains, and confidence-gated rename
- [x] #5 Full suite green; pypeeker check exits 0
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Move _bare_type_name -> resolve.bare_type_name (context.py imports it). resolve_reference: factor _resolve_attr(receiver_root, attr); after module/class container miss, if root sym is PARAMETER/VARIABLE with type_annotation, bare_type_name -> resolve in receiver module via _members -> class -> member. Tests: annotated param receiver in find_all_references + call graph; unannotated/builtin/external no-crash. Note deferred gap as draft. suite+check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
resolve.py: moved bare_type_name from analysis/context.py (single source; analysis->resolve already allowed) and added a type-dereference branch to _resolve_attr: when the receiver root is a PARAMETER/VARIABLE with a type_annotation, normalize the bare type name, resolve it in the receiver module via _members to a class, then look up the member. find_all_references and the call graph pick this up automatically (they use resolve_reference). plan-rename does NOT use resolve_reference, so it is unchanged (query-only as intended).

Verified on pypeeker: IndexStore.load 0 -> 4 refs, IndexStore.save 0 -> 1, find_symbol 0 -> 1; zero-ref method candidates 94 -> 86. Residual is out-of-scope cases (multi-hop self.attr chains, constructor-typed locals, Click callbacks, test-only API) filed as the Gap A part 3 draft.

Tests: 421 pass (annotated/Optional-annotated param receivers resolve; unannotated does not; call-graph annotated edge). pypeeker check exits 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Resolve annotated instance-receiver attribute calls at the query layer (Gap A, part 2). For store.save() where store: IndexStore, resolve_reference now dereferences the receiver value -> its declared type -> the class -> the member, reusing the bare-type-name normalizer (moved to resolve.py for single source) and the existing _members map. find_all_references and the call graph gain these edges; plan-rename is intentionally left on its exact-binding rule (query-only, since annotation-based receiver inference is best-effort).

User impact: instance-method usages on annotated receivers are now discoverable. On pypeeker, IndexStore.load went 0 -> 4 references and zero-reference method candidates fell 94 -> 86; the remainder are multi-hop self.attr chains, constructor-typed locals, and entry points/test-only API (deferred).

Tests: 421 pass; pypeeker check exits 0. Constructor inference, multi-hop chains, and confidence-gated rename are filed as a low-priority follow-up draft.
<!-- SECTION:FINAL_SUMMARY:END -->
