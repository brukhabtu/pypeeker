---
id: TASK-54
title: 'plan-extract-variable: first complex refactor (end-to-end proof)'
status: Done
assignee:
  - '@claude'
created_date: '2026-05-25 13:02'
updated_date: '2026-05-25 17:15'
labels:
  - refactor
dependencies:
  - TASK-51
  - TASK-52
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The smallest complex refactor, to validate the analyze-on-index / rewrite-on-CST / emit-transaction pipeline end to end. Given a file:line:col span identifying an expression and a new name, insert "name = <expr>" on a new line above the enclosing statement (matching indentation) and replace the selected expression with name. Produces a transaction applied by the existing machinery; preserves surrounding formatting (CST byte edits). New CLI command plan-extract-variable.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Given an expression span + a name, the planner emits a transaction that inserts "name = <expr>" above the statement and replaces the expression with the name
- [x] #2 Indentation of the inserted line matches the statement; unrelated formatting/comments are preserved (CST byte edits, not unparse)
- [x] #3 CLI plan-extract-variable wires to the planner; apply produces runnable code; tests cover an end-to-end extract
- [x] #4 New name is validated; invalid spans/non-expressions error clearly
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
refactor/extract.py: ExtractVariablePlanner.plan(file, start, end, name): read+hash source (not stale), parse CST, node_spanning(start,end), reject non-expression, enclosing_statement, build INSERT (indent+name=expr) at statement line-start + REPLACE expr->name, save transaction. CLI plan-extract-variable FILE START END NAME (line:col 0-indexed). Tests: planner emits 2 edits, apply yields runnable extracted code, formatting preserved, errors. suite+check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
refactor/extract.py ExtractVariablePlanner.plan(file,start,end,name): reads+hashes source, parses CST, node_spanning(start,end) -> expression (rejects non-expression/module/block/defs), enclosing_statement, emits INSERT (indent + "name = expr" + newline) at the statement line-start and REPLACE expr->name, saves a transaction. CLI plan-extract-variable FILE START END NAME (0-indexed line:col). Verified end-to-end via CLI: return foo(bar)+2 -> value=foo(bar); return value+2, formatting preserved. 475 tests pass; pypeeker check exits 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
First complex refactor end-to-end: plan-extract-variable. Selecting an expression (start..end positions) introduces a new local on the line above (matching indentation) and replaces the selection with the name, via INSERT + REPLACE byte edits applied by the existing transaction machinery - so surrounding formatting/comments are preserved. This validates the full analyze/rewrite-on-CST -> transaction -> apply pipeline (foundations TASK-51 + TASK-52). 475 tests pass; pypeeker check exits 0.
<!-- SECTION:FINAL_SUMMARY:END -->
