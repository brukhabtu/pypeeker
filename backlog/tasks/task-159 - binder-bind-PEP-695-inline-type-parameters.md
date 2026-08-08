---
id: TASK-159
title: 'binder: bind PEP 695 inline type parameters'
status: Done
assignee:
  - '@claude'
created_date: '2026-08-08 19:59'
updated_date: '2026-08-08 22:53'
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
- [x] #1 References to PEP 695 type parameters resolve for functions, methods, classes, and type aliases
- [x] #2 A ledger entry records the substrate fix
- [x] #3 Full four-step gate green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v4 in /home/user/pypeeker-wt159, parallel with TASK-160 (disjoint files).
<!-- SECTION:PLAN:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Bound PEP 695 inline type parameters end-to-end (PR #128).

- TYPE_PARAMETER symbols in the definition's own scope for functions, methods, classes; return-annotation and base-class visits moved after the scope push (the implicit annotation scope, made practical).
- type X[T] = ... declares the alias and binds parameters; splat and constrained forms bind with bounds producing references; PEP 696 defaults surface as the grammar's syntax error via FileIndex.errors.
- One deliberate ScopeStack.resolve exception makes class type parameters visible from method bodies, yielding to explicit global/nonlocal (adversarial-round catch, pinned byte-level).
- dsl/corpus.py TypeVar workaround replaced by real def memo[T] — the live proof. Ledger entry records the substrate fix.

Tests: 29 new (3,424 total); four-step gate PASS in worktree and after merge.
<!-- SECTION:FINAL_SUMMARY:END -->
