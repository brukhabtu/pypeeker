---
id: TASK-52
title: 'Refactor foundation: CST rewrite utilities (refactor/cst.py)'
status: To Do
assignee: []
created_date: '2026-05-25 13:01'
labels:
  - refactor
  - foundation
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
A syntactic layer for refactoring: re-parse a file via the Python adapter (tree-sitter CST) and provide helpers to (a) locate the statement node(s) spanning a file:line[:col] range, (b) locate the smallest expression node at a position, (c) compute byte offsets for nodes, and (d) build EditEntry edits from node spans + new text. Transient (re-parsed per refactor, never persisted - the index stays for analysis). Depends only on adapters + models; no analysis dependency.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Given a parsed tree and a line/col range, return the enclosing statement node(s) and the smallest expression node at a position
- [ ] #2 Helpers compute byte offsets for a node and construct REPLACE/INSERT/DELETE EditEntry values from node spans
- [ ] #3 Module re-parses on demand and persists nothing; respects import-boundaries (refactor -> adapters, models)
<!-- AC:END -->
