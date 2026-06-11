---
id: TASK-69
title: >-
  Adapter layer honesty: binder is the Python adapter; shrink protocol; consume
  or trim capabilities
status: To Do
assignee: []
created_date: '2026-06-11 15:47'
labels:
  - adapters
  - binder
  - docs
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
LanguageAdapter's structural methods (capabilities, is_scope_node, is_declaration_node, is_reference_node, extract_name) are never called in src; the binder type-hints concrete PythonAdapter and hardcodes tree-sitter-python node types; refactor/cst.py owns CST edits the doc assigns to adapters. Make the architecture honest: the language-agnostic seam is FileIndex, and the binder+cst utilities ARE the Python adapter.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The protocol is shrunk to what a second language would actually provide (parse/bind to FileIndex + CST edit helpers), or unused methods are removed; no dead protocol surface remains
- [ ] #2 Binder/cst placement or documentation makes 'binder = Python adapter' explicit (move under adapters/python or equivalent doc + layering update)
- [ ] #3 Capability enum is either consumed by at least one real consumer or trimmed; decision recorded
- [ ] #4 architecture.md Layer 1 section matches the implementation; import-boundaries allow-list updated if files move
<!-- AC:END -->
