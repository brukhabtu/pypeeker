---
id: TASK-24
title: 'Binder: resolve forward references to module-level symbols'
status: To Do
assignee: []
created_date: '2026-05-12 15:42'
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
- [ ] #1 A function body can reference any module-level symbol regardless of declaration order in the source
- [ ] #2 Same for class bodies referencing module-level symbols
- [ ] #3 Test: file with f() at top calling g() defined below — reference resolves
- [ ] #4 pypeeker check on its own source no longer reports unresolved refs for module-local helpers (_symbols_by_id, _leaf_method, _classify_receiver, etc.)
<!-- AC:END -->
