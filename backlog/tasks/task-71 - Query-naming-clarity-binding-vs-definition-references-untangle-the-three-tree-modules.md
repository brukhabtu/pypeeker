---
id: TASK-71
title: >-
  Query naming clarity: binding vs definition references; untangle the three
  tree modules
status: To Do
assignee: []
created_date: '2026-06-11 15:47'
labels:
  - query
  - clarity
dependencies:
  - TASK-62
priority: low
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
find_references (exact binding-id match) vs find_all_references (resolved-definition match) differ in a way the names do not express, and the CLI --all flag has the same problem. Separately, pypeeker/tree.py, models/tree.py, and storage/tree_store.py are a three-way 'tree' collision; the top-level one is really tree reconciliation/build logic.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Query method names (or at minimum docstrings + CLI help) express the binding-vs-definition distinction; CLI refs help text disambiguates --all
- [ ] #2 pypeeker/tree.py is renamed or repackaged so the three tree modules are distinguishable; imports and layering config updated
- [ ] #3 Full test suite passes
<!-- AC:END -->
