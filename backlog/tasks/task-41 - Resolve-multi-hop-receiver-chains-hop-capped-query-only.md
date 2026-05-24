---
id: TASK-41
title: 'Resolve multi-hop receiver chains, hop-capped (query-only)'
status: Done
assignee:
  - '@claude'
created_date: '2026-05-24 03:49'
updated_date: '2026-05-24 03:50'
labels:
  - analysis
  - index
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Gap A multi-hop (from DRAFT-2). Resolve attribute/method access through a chain of receivers - self.field.method(), state.scope_stack.push() - by walking the receiver chain: from the root container, each intermediate name resolves to a member whose type gives the next container, then the leaf attr is looked up. Capped at a small hop limit to bound work and avoid low-confidence long chains. Query-only (find_all_references, call graph); plan-rename unchanged. This is the gap that polluted the CLI-reachability survey (scope_stack/adapters methods looked unreachable because state.scope_stack.push() produced no edge).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 resolve_reference walks a receiver chain up to a capped length: root container (module/class/typed-or-self receiver) -> intermediate members via their types -> leaf member; over the cap returns None
- [x] #2 self.field.method() and param.field.method() (with annotated/inferred field and param types) resolve to the field type's method; self/cls roots resolve to the enclosing class
- [x] #3 find_all_references and the call graph gain multi-hop edges; CLI-reachability of scope_stack/adapter methods improves measurably; plan-rename unchanged
- [x] #4 Unresolvable/over-cap/dynamic chains return None without error; full suite green; pypeeker check exits 0
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
resolve.py: add _MAX_RECEIVER_HOPS, _container_of (module/class/typed-param/self-class), generalize _resolve_attr to walk chain[1:] via field types to leaf. Tests: self.field.method, param.field.method, over-cap None, self/cls root. Re-measure CLI reachability. suite+check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
resolve.py: added _MAX_RECEIVER_HOPS=3 and _container_of(symbol_id) (module/class -> itself; typed param/var -> its type class; self/cls -> enclosing class). _resolve_attr now walks receiver_chain[1:] resolving each name to a member and following its type to the next container, then looks up the leaf attr. Over the cap returns None.

Real-code win: ScopeStack.push/pop went 0 -> 6 refs each (called as state.scope_stack.push()). CLI-reachability improved 86/207 -> 110/209. (Remaining unreachable is dominated by the analysis package, which has no CLI command - a wiring fact, not a resolution miss.)

Tests: 432 pass (self.field.method, param.field.method, over-cap None, call-graph self.field edge). pypeeker check exits 0. Query-only: plan-rename unchanged.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Resolve multi-hop receiver chains with a hop cap (query-only). Attribute/method access through a chain - self.field.method(), state.scope_stack.push() - now resolves by walking the receiver chain: from the root container (module, class, or the class behind a typed/self receiver), each intermediate name resolves to a member whose type gives the next container, then the leaf is looked up. Capped at 3 hops to bound work and avoid low-confidence long chains.

Added _container_of (handles module/class/typed-param/var and self/cls -> enclosing class) and generalized _resolve_attr to iterate the chain. find_all_references and the call graph gain these edges; plan-rename is unchanged.

Impact: ScopeStack.push/pop went 0 -> 6 references each (state.scope_stack.push()); CLI-reachability rose 86/207 -> 110/209. The residual unreachable set is mostly the analysis package, which no CLI command invokes (a wiring fact, not a resolution gap).

Tests: 432 pass; pypeeker check exits 0. Only confidence-gated rename remains in DRAFT-2.
<!-- SECTION:FINAL_SUMMARY:END -->
