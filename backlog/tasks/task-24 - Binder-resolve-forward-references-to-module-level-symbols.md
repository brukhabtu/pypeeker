---
id: TASK-24
title: 'Binder: resolve forward references to module-level symbols'
status: Done
assignee:
  - '@claude'
created_date: '2026-05-12 15:42'
updated_date: '2026-05-19 12:27'
labels:
  - binder
  - linter
dependencies:
  - TASK-22
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Functions/classes defined LATER in a module are not resolved when referenced from earlier function bodies. Example: in analysis/calls.py, `bare_calls()` (defined early) calls `_symbols_by_id` (defined later) — that reference is currently unresolved. Python's module resolution is two-pass: declarations are collected before bodies are executed, so any module-level symbol is visible anywhere in the module body.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A function body can reference any module-level symbol regardless of declaration order in the source
- [x] #2 Same for class bodies referencing module-level symbols
- [x] #3 Test: file with f() at top calling g() defined below — reference resolves
- [x] #4 pypeeker check on its own source no longer reports unresolved refs for module-local helpers (_symbols_by_id, _leaf_method, _classify_receiver, etc.)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Approach: end-of-module fixup, not two-pass traversal. Single-pass walk stays unchanged; after visit_module finishes, retry any unresolved bare-name references against the now-fully-populated module scope.

1. Read binder/state.py and binder/binder.py to find the module scope reference and the right place to hook the fixup.
2. Add _resolve_module_forward_refs(state) in binder/binder.py: build {name: symbol} for module-scope symbols, walk state.references, for each resolved=False ref with a bare symbol_id (no colon, no <bracket>), if the name matches a module symbol, replace the ref with one pointing at the resolved symbol_id and resolved=True.
4. Call it at the end of visit_module after popping the scope.
5. Tests: forward call within module body, forward class reference, nested function body referencing a later module-level helper, shadowing case (local declaration after use stays unresolved).
6. Re-index pypeeker; confirm _symbols_by_id/_leaf_method/_classify_receiver/ContextError/etc. now resolve.
7. Commit, push, PR, merge.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Implemented as end-of-module fixup, not two-pass traversal. Single visit_module hook + 25-line _resolve_module_forward_refs() function; no changes to existing visitors.
- Fixup also retries refs that resolved to <builtins>.X — when a user shadows a builtin at module scope (e.g. defines their own `len`), an earlier reference rebinds to the local. Caught by a test.
- 8 new tests covering forward function calls, forward class refs, forward module constants, nested-function bodies reaching module helpers, shadowing edge cases (parameter still wins; genuinely undefined stays unresolved), and builtin shadowing.
- Full suite 350 passed.
- End-to-end: every originally-targeted forward ref now resolves (_symbols_by_id, _leaf_method, _classify_receiver, _resolve_function, ContextError, ...). The remaining ~48 no-unresolved-refs are short comprehension variables (s, p, c, name, key, value, ...) — separate binder gap filed as TASK-25.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
End-of-module fixup that re-binds unresolved bare-name references against the now-fully-populated module scope.

## Why
Python is two-pass at module scope: every top-level def/class/assignment is registered before any function body runs. The binder walks once top-to-bottom, so a function body that called a helper defined later in the same file produced an unresolved reference. \`pypeeker check\` surfaced hundreds of these false positives on the project's own source (e.g. \`bare_calls\` calling \`_symbols_by_id\` defined further down in \`analysis/calls.py\`).

## What
- New \`_resolve_module_forward_refs(state)\` in \`binder/binder.py\` (~25 lines).
- Called once at the end of \`visit_module\`, after children have been visited and the module scope popped.
- Builds \`{name: Symbol}\` for module-scope symbols, walks \`state.references\` once, replaces unresolved bare-name refs (and refs that resolved to \`<builtins>.X\`) whose name now matches a module-level symbol.
- Skips already-resolved refs that came from inner scopes (function-local lookups still take priority — the parameter \`x\` in \`def f(x)\` is unaffected by a later module-level \`x = 1\`).
- Also rebinds builtin-resolved refs when the user defines a shadowing module-level symbol: \`def caller(): return len([1,2]); def len(x): return 0\` — the call binds to the local, not \`<builtins>.len\`.

Approach chosen over two-pass module traversal because it's strictly additive: no changes to existing visitors, no risk of regressing the dozens of binder paths, and it naturally handles forward refs from nested function bodies (which a strict two-pass module walk wouldn't catch).

## Tests
8 new in \`tests/test_binder_forward_refs.py\`:
- Forward call to helper defined below
- Two callers both resolve
- Function references class defined below
- Function references module constant defined below
- Nested function body resolves module helper
- Local parameter still wins (no rebinding when use-time lookup found a local)
- Genuinely undefined names stay unresolved
- Local function shadowing a builtin name takes priority over the builtin

Full suite: 350 passed.

## End-to-end
Every originally-targeted forward ref now resolves: \`_symbols_by_id\`, \`_leaf_method\`, \`_classify_receiver\`, \`_resolve_function\`, \`ContextError\`, ... all show up as resolved.

## Follow-up filed
**TASK-25**: the remaining \`no-unresolved-refs\` violations on pypeeker's source (\`s\`, \`name\`, \`key\`, \`value\`, \`p\`, ...) are comprehension/generator variables that aren't being declared into the comprehension scope. Different bug from forward refs; tracked separately.
<!-- SECTION:FINAL_SUMMARY:END -->
