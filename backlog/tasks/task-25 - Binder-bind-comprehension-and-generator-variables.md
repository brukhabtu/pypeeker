---
id: TASK-25
title: 'Binder: bind comprehension and generator variables'
status: To Do
assignee: []
created_date: '2026-05-19 12:26'
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
- [ ] #1 for_in_clause target identifiers are declared as VARIABLE symbols in the comprehension scope
- [ ] #2 The comprehension's element/key/value expressions resolve those names to the declared variable, not as unresolved
- [ ] #3 Tested with: list comp, set comp, dict comp, generator expression, nested comp (`x for x in xs for y in ys`), and tuple unpacking targets (`k, v for k, v in items`)
- [ ] #4 pypeeker check on its own source no longer reports unresolved refs for short comprehension variable names (s, p, c, name, key, value, prefix, f, r, ids)
<!-- AC:END -->
