---
id: TASK-59
title: 'models: symbol_id module owning the id grammar and sentinel prefixes'
status: To Do
assignee: []
created_date: '2026-06-11 15:46'
labels:
  - models
  - clarity
dependencies: []
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The symbol-id grammar (module.path:Scope.Chain:local$N) and the sentinel prefixes <builtins>. / <unresolved>. are the system's core abstraction but are built in binder/scope_stack+helpers and re-parsed by ad-hoc string surgery in resolve.py, query/engine.py, analysis/calls.py, analysis/writes.py, check/rules.py, and refactor/extract.py. Reference.symbol_id is overloaded across four meanings (resolved id, bare unresolved name, builtin, unresolved attribute) and every consumer re-derives the case. Centralize the grammar in one models module.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A models-level symbol-id module exposes the prefix constants and helpers (at minimum: module_of, leaf_name, is_builtin, is_unresolved_attr, shadow-suffix handling)
- [ ] #2 resolve, query.engine, analysis.calls, analysis.writes, check.rules, refactor.extract, and binder.helpers consume the shared module; locally duplicated UNRESOLVED_PREFIX/BUILTINS_PREFIX constants are removed
- [ ] #3 No behavioral change: full test suite passes without test edits (except imports/names)
<!-- AC:END -->
