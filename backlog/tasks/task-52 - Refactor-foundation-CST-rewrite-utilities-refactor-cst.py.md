---
id: TASK-52
title: 'Refactor foundation: CST rewrite utilities (refactor/cst.py)'
status: Done
assignee:
  - '@claude'
created_date: '2026-05-25 13:01'
updated_date: '2026-05-25 17:13'
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
- [x] #1 Given a parsed tree and a line/col range, return the enclosing statement node(s) and the smallest expression node at a position
- [x] #2 Helpers compute byte offsets for a node and construct REPLACE/INSERT/DELETE EditEntry values from node spans
- [x] #3 Module re-parses on demand and persists nothing; respects import-boundaries (refactor -> adapters, models)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
refactor/cst.py: parse(source)->root via PythonAdapter; expression_at(root,line,col); enclosing_statement(node); node_text; statement_line_start/indent helpers; replace_edit/insert_edit building EditEntry from node byte spans. Tests in test_refactor_cst.py. suite+check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
refactor/cst.py: parse(source) -> CST root (via PythonAdapter); expression_at(root,line,col) and node_spanning(root,start,end) locate the smallest named node at a position/range; enclosing_statement walks to the statement (child of block/module); node_text/line_start_byte/indent_of helpers; replace_edit and insert_edit build EditEntry directly from tree-sitter node byte spans (start_byte/end_byte). Transient (re-parse per call), depends only on adapters + models. Tests in test_refactor_cst.py. 470 tests pass; pypeeker check exits 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
CST rewrite utilities for refactoring (refactor/cst.py). Re-parses a file to the tree-sitter CST on demand (never persisted) and provides byte-precise helpers: locate the expression node at a position or spanning a range, find the enclosing statement, read node text/indent, and build REPLACE/INSERT EditEntry values straight from node byte offsets. This is the syntactic layer for the analyze-on-index / rewrite-on-CST pipeline. 470 tests pass; pypeeker check exits 0.
<!-- SECTION:FINAL_SUMMARY:END -->
