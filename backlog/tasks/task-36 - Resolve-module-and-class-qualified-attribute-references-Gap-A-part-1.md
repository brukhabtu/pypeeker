---
id: TASK-36
title: 'Resolve module- and class-qualified attribute references (Gap A, part 1)'
status: Done
assignee:
  - '@claude'
created_date: '2026-05-24 01:51'
updated_date: '2026-05-24 01:54'
labels:
  - analysis
  - binder
  - index
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Dogfooding follow-up (Gap A). The binder resolves self.attr to a class member but leaves every other attribute/method access as <unresolved>.<attr> (it is per-file and cannot see other modules). It does, however, capture receiver_root_symbol_id and receiver_chain. Consequently find_references/find_all_references and the call graph miss method/qualified-function usages (e.g. store.save(), othermod.func(), ScopeKind.MODULE), which is why dogfooding reported ~94 false-positive dead methods.

This closes the determinable part of Gap A at the resolver/query layer: a single-hop attribute access receiver.attr is resolved to the member named attr of the receiver\'s container when the receiver resolves to a known module or class/enum. Because a class scope_id equals its symbol_id, module members and class members both live under their container via parent_scope_id, so one lookup (member named attr with parent_scope_id == container) covers both. Instance-type inference (x: Foo; x.bar()) and multi-hop chains (a.b.c) are explicit follow-ups.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 CrossModuleResolver gains resolve_reference(ref): a single-hop <unresolved>.attr access whose receiver_root resolves to a module or class/enum resolves to that container member named attr (then through re-exports); bare and already-resolved references fall back to resolve_definition unchanged
- [x] #2 find_all_references includes attribute/method usages: e.g. usages of a method resolve to it across modules, and module-qualified function calls (othermod.func()) are matched
- [x] #3 The call graph uses resolve_reference so module-qualified and class-qualified calls produce edges (in addition to bare-name and self. calls)
- [x] #4 Instance-typed receivers (x: Foo; x.bar()) and multi-hop receiver chains (a.b.c) are out of scope and left unresolved (documented as follow-up); external/unknown receivers resolve to nothing without error
- [x] #5 Full suite green; pypeeker check exits 0; existing find_references/rename/call-graph/purity behavior is unchanged except for the newly-resolved edges
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
resolve.py: build _members[parent_scope_id][name]=symbol (subsumes _module_names; update _resolve_chain). Add resolve_reference(ref): if symbol_id startswith <unresolved>. and single-hop receiver, container=resolve_definition(receiver_root); member=_members[container][attr]; return resolve_definition(member.id) else fallback resolve_definition(symbol_id). find_all_references uses resolve_reference per ref. graph.py callee=resolve_reference(ref). Tests: module-qualified call/usage, class/enum member, find_all_references method usages, call-graph module-qualified edge. Run suite+check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
resolve.py: generalized _module_names into _members[parent_scope_id][name] (covers module members AND class members, since a class scope_id == its symbol_id). Added resolve_reference(ref): for a single-hop <unresolved>.attr access, container=resolve_definition(receiver_root); if container has a member named attr, resolve to it (through re-exports); otherwise fall back to resolve_definition(symbol_id). find_all_references now canonicalizes each ref via resolve_reference. graph.py call_graph uses resolve_reference and no longer pre-filters on ref.resolved (attribute calls are recorded unresolved but resolvable via receiver; function_ids membership filters).

Verified on pypeeker: SymbolKind.MODULE class-member access resolves (4 refs); unused-import false positives 11 -> 7 (cumulative 48 -> 7 across the annotation + attribute fixes). Remaining 7 are instance-typed receivers / multi-hop / function-local imports (documented follow-ups).

Tests: 417 pass (module-qualified call+usage, class/enum member, call-graph module-qualified edge, external-receiver no-crash). pypeeker check exits 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Resolve module- and class-qualified attribute references at the query layer (Gap A, part 1). The binder resolves self.attr but emits <unresolved>.attr for every other attribute access (it is per-file); it does capture receiver_root_symbol_id + receiver_chain. This adds resolver-layer resolution: for a single-hop receiver.attr, if the receiver resolves to a known module or class/enum, the attribute is resolved to that container member.

Key simplification: because a class scope_id equals its symbol_id and module-level symbols carry parent_scope_id == module path, one member map keyed by parent_scope_id covers both module members (othermod.func()) and class members (Enum.MEMBER, Class.method).

Changes:
- CrossModuleResolver: _members[parent_scope_id][name]; resolve_reference(ref) canonicalizes bare, aliased, barrel, and qualified-attribute references uniformly.
- find_all_references and the call graph use resolve_reference, so method/qualified-function usages and call edges are now found (call_graph no longer pre-filters unresolved attribute calls).

User impact: find_references/find_all_references and the transitive-purity call graph now see module-qualified and class-qualified usages they previously missed. On pypeeker itself, unused-import false positives fell from 48 (pre-work) to 7.

Out of scope (follow-ups): instance-typed receivers (x: Foo; x.bar()) and multi-hop chains (a.b.c). These keep instance-method false positives (e.g. store.save()) until type propagation is added.

Tests: 417 pass; pypeeker check exits 0.
<!-- SECTION:FINAL_SUMMARY:END -->
