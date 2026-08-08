---
id: TASK-159
title: 'binder: bind PEP 695 inline type parameters'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-08-08 19:59'
updated_date: '2026-08-08 20:00'
labels:
  - binder
  - dsl
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found during TASK-155: def memo[T](...) leaves T unbound — the binder does not bind a function's, method's, or class's inline type-parameter list (PEP 695), so annotations referencing them produce genuine no-unresolved-refs findings (two real ones observed on Corpus.memo before a TypeVar workaround). Bind type parameters as symbols in the appropriate scope so references to them resolve, for functions, methods, classes, and type aliases. NOTE: this shifts the frozen oracle's observable output the same way the typed-variadic fix did (both engines read the same binder) — a dsl-rewrite.md ledger entry recording the substrate fix is REQUIRED in the same change.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 References to PEP 695 type parameters resolve for functions, methods, classes, and type aliases
- [ ] #2 A ledger entry records the substrate fix
- [ ] #3 Full four-step gate green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v4 in /home/user/pypeeker-wt159, parallel with TASK-160 (disjoint files).
<!-- SECTION:PLAN:END -->
