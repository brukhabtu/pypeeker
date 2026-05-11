---
id: TASK-20
title: >-
  Binder: treat Python builtins as resolved (frozenset, property, len,
  annotations, etc.)
status: To Do
assignee: []
created_date: '2026-05-11 12:30'
labels:
  - binder
  - followup
dependencies:
  - TASK-19
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Running 'pypeeker check' against pypeeker's own source surfaces many false-positive no-unresolved-refs violations on Python builtins (frozenset, property, len, annotations, ValueError, list, ...). The binder should mark references to known builtins as resolved=True. Also treat 'from __future__ import annotations' specially.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 References to Python builtins are marked resolved=True in the index
- [ ] #2 from __future__ import annotations does not produce an unresolved reference
- [ ] #3 pypeeker check on pypeeker's own source no longer reports builtin false positives
<!-- AC:END -->
