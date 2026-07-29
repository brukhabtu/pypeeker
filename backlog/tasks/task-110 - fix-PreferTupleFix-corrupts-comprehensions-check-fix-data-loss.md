---
id: TASK-110
title: 'fix: PreferTupleFix corrupts comprehensions (check --fix data loss)'
status: Done
assignee:
  - '@claude'
created_date: '2026-07-29 17:36'
updated_date: '2026-07-29 18:34'
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
- [x] #1 prefer-tuple's fix never emits the ( ... , ) shape for a comprehension; it either skips comprehensions or rewrites them to a semantically-equivalent tuple form (e.g. tuple(...)).
- [x] #2 Applying 'check --fix' to a module containing a never-mutated list comprehension leaves it valid and behavior-preserving (verified by a regression test).
- [x] #3 'check --fix' never leaves the working tree in a state that breaks 'pypeeker rollback' (e.g. the applier verifies re-parseability, or rollback is robust to a broken source).
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Locate the corruption: byte scanner cannot distinguish [x] from [x for x in y].
2. Root: _literal_list_type tags comprehensions as list; flagging is intended (tested), only the fix is wrong.
3. Teach the scanner to track () [] {} and detect a top-level for keyword.
4. PreferTupleFix emits tuple(...) for comprehensions, bracket-swap for element lists.
5. Regression tests + dogfood check --fix on real source.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Fixed the fix, not the rule (flagging comprehensions is intended per test_comprehension_local_flagged). Scanner now tracks all bracket kinds (also fixes [(a,b)] nested-comma miscount) and detects comprehensions via a word-boundary-guarded top-level `for`. Dogfood: check --fix applied 38 fixes to real src with prefer-tuple live; tool still runs and all src compiles (previously corrupted 16 files incl. cli.py).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
PreferTupleFix corrupted list comprehensions: `[c for c in xs]` became `(c for c in xs,)` (a 1-tuple wrapping a generator / SyntaxError), because the byte scanner saw the same has_elements+zero-top-level-commas shape as a single-element list `[x]`. Run repo-wide by `check --fix` this broke 16 files including cli.py, and the SyntaxError stopped pypeeker's own rollback.

Fix: the scanner now tracks nesting across () [] {} (so nested commas/for aren't counted top-level — also fixes `[(a, b)]`) and flags a comprehension via a word-boundary-guarded top-level `for`; PreferTupleFix emits `tuple(...)` for comprehensions and the bracket swap only for element lists. Added regression tests (comprehension->tuple(...), format()/for boundary, nested-tuple element) and dogfooded `check --fix` on real source: 38 fixes applied, tool still runs, all src compiles.
<!-- SECTION:FINAL_SUMMARY:END -->
