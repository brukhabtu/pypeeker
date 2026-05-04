---
id: TASK-18
title: Drop Binder class facade; migrate call sites to bind() function
status: To Do
assignee: []
created_date: '2026-05-04 13:04'
updated_date: '2026-05-04 13:04'
labels: []
dependencies:
  - TASK-17
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
After TASK-17, the Binder class is a vestigial facade — every method delegates to a module-level function. Drop the class entirely and update the three call sites (cli.py, applier.py, tests/conftest.py) to use bind() directly. binder/__init__.py exports bind() instead of Binder.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Binder class removed from src/pypeeker/binder/binder.py
- [ ] #2 src/pypeeker/binder/__init__.py exports bind (and BinderState if needed publicly) instead of Binder
- [ ] #3 src/pypeeker/cli.py: index command uses bind() directly
- [ ] #4 src/pypeeker/refactor/applier.py: _reindex_files uses bind() directly
- [ ] #5 tests/conftest.py: bind_source and bind_fixture fixtures use bind() directly
- [ ] #6 All 287 tests pass after migration
- [ ] #7 Re-indexing pypeeker's own src/ produces identical output to TASK-17's state
<!-- AC:END -->
