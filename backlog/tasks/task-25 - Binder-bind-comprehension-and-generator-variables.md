---
id: TASK-25
title: 'Binder: bind comprehension and generator variables'
status: Done
assignee:
  - '@claude'
created_date: '2026-05-19 12:26'
updated_date: '2026-05-22 22:44'
labels:
  - binder
  - linter
dependencies:
  - TASK-24
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Variables introduced by comprehensions and generator expressions (e.g. `name for name in dir(builtins)`, `s for s in symbols`, `p.rstrip() for p in prefixes`) aren't being declared into the comprehension scope, so the iteration variable shows up as an unresolved reference at every use inside the comprehension body. Need to inspect visit_comprehension and ensure the for_in_clauses' target identifiers are declared as VARIABLE symbols in the comprehension scope before the rest of the comprehension is visited.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 for_in_clause target identifiers are declared as VARIABLE symbols in the comprehension scope
- [x] #2 The comprehension's element/key/value expressions resolve those names to the declared variable, not as unresolved
- [x] #3 Tested with: list comp, set comp, dict comp, generator expression, nested comp (`x for x in xs for y in ys`), and tuple unpacking targets (`k, v for k, v in items`)
- [x] #4 pypeeker check on its own source no longer reports unresolved refs for short comprehension variable names (s, p, c, name, key, value, prefix, f, r, ids)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Read binder/binder.py visit_comprehension to understand current handling.
2. Inspect tree-sitter parse for list/set/dict comp and generator expression — find the for_in_clause structure and target identifier(s).
3. In visit_comprehension, before visiting child expressions, walk the for_in_clauses and declare each target identifier as a VARIABLE in the comprehension scope (handling tuple unpacking via extract_targets helper).
4. Tests: list/set/dict/generator comp, multiple for clauses, tuple unpacking targets, walrus operator interaction (later).
5. Re-index pypeeker, confirm no more comp-var unresolved refs.
6. Commit, PR, merge.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Root cause was twofold:
  1. visit_comprehension iterated children in source order — element first, for-clauses after — so the element was bound before the loop targets existed in scope. Fixed by doing two passes: pass 1 processes for_in_clauses (declaring targets, visiting iterables with the first-in-enclosing-scope semantic), pass 2 visits the element and if_clauses.
  2. When a generator expression is the SOLE argument to a call (e.g. `frozenset(name for name in dir(...))`), tree-sitter puts the generator_expression directly in the call's `arguments` field instead of wrapping it in an argument_list. visit_call was iterating args_node.children, which descended into the gen-exp body BEFORE visit_comprehension ever set up the scope. Fixed by dispatching the arguments node as a whole when it isn't an argument_list.
- 11 new tests covering: list/set/dict comp and gen-exp targets in element + filter; multiple for-clauses where second iterable references first target; tuple unpacking (k, v) and nested ((a, b), c); enclosing scope still resolves first iterable.
- Full suite 361 passed.
- End-to-end: pypeeker check went from 99 → 50 violations on its own source. Zero no-unresolved-refs remaining. The 50 are all legitimate require-docstrings findings.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Bind comprehension and generator-expression iteration variables.

## Why
The binder already created a comprehension scope and called \`declare_variable\` on the for-clause targets, but iterated child nodes in source order. Tree-sitter emits the comprehension as element ⇒ for_in_clauses ⇒ if_clauses, so the element expression was being bound BEFORE the targets were declared. Every \`x\` in \`[x for x in xs]\` came out unresolved.

A second, subtler issue: when a generator expression is the sole argument to a call (e.g. \`frozenset(name for name in dir(...))\`), tree-sitter puts the \`generator_expression\` node directly in the call's \`arguments\` field rather than wrapping it in an \`argument_list\`. \`visit_call\` was iterating that node's children — which descended into the generator body before \`visit_comprehension\` could establish the scope.

## What
- \`visit_comprehension\` now makes two passes over the children:
  - Pass 1: every \`for_in_clause\` — declare its targets (handling tuple unpacking via \`extract_targets\`), visit its iterable (first one in the enclosing scope per Python semantics, subsequent ones in the comprehension scope).
  - Pass 2: element expression and \`if_clause\` filters.
- \`visit_call\` dispatches a non-\`argument_list\` \`arguments\` node through \`visit_node\` as a whole, so the comprehension/genexpr scope is established before its body is bound.

## Tests
11 new in \`tests/test_binder_comprehensions.py\`:
- List / set / dict comp target resolution in element and filter
- Generator expression target resolution
- Generator expression as a sole call argument (the exact pattern from \`binder/helpers.py\`)
- Multiple for-clauses where the second iterable references the first target
- Tuple unpacking targets \`(k, v)\` and nested \`((a, b), c)\`
- First iterable resolves in the enclosing scope

Full suite: 361 passed.

## End-to-end
Running \`pypeeker check\` on the project itself: violations went from 99 → 50. **Zero \`no-unresolved-refs\` left.** The 50 remaining are all legitimate \`require-docstrings\` findings — the linter is now doing its actual job.
<!-- SECTION:FINAL_SUMMARY:END -->
