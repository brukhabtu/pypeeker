---
id: TASK-110
title: 'fix: PreferTupleFix corrupts comprehensions (check --fix data loss)'
status: To Do
assignee: []
created_date: '2026-07-29 17:36'
labels:
  - bug
  - check
  - refactor
dependencies: []
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
check --fix's PreferTupleFix rewrites a list literal [ ... ] to ( ... , ) blindly. For a comprehension bound to a never-mutated list (x = [a for a in b]) this produces (a for a in b,) — a one-tuple wrapping a generator, or a SyntaxError — corrupting the file. Found by dogfooding 'check --fix' on pypeeker's own source: it corrupted 16 files including cli.py, and the resulting SyntaxError in cli.py meant 'pypeeker rollback' itself could not run (the tool cannot recover from a fix that breaks its own entry point). This is a correctness/data-loss defect in the fix engine.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 prefer-tuple's fix never emits the ( ... , ) shape for a comprehension; it either skips comprehensions or rewrites them to a semantically-equivalent tuple form (e.g. tuple(...)).
- [ ] #2 Applying 'check --fix' to a module containing a never-mutated list comprehension leaves it valid and behavior-preserving (verified by a regression test).
- [ ] #3 'check --fix' never leaves the working tree in a state that breaks 'pypeeker rollback' (e.g. the applier verifies re-parseability, or rollback is robust to a broken source).
<!-- AC:END -->
