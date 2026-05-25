---
id: TASK-54
title: 'plan-extract-variable: first complex refactor (end-to-end proof)'
status: To Do
assignee: []
created_date: '2026-05-25 13:02'
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
- [ ] #1 Given an expression span + a name, the planner emits a transaction that inserts "name = <expr>" above the statement and replaces the expression with the name
- [ ] #2 Indentation of the inserted line matches the statement; unrelated formatting/comments are preserved (CST byte edits, not unparse)
- [ ] #3 CLI plan-extract-variable wires to the planner; apply produces runnable code; tests cover an end-to-end extract
- [ ] #4 New name is validated; invalid spans/non-expressions error clearly
<!-- AC:END -->
