---
id: TASK-18
title: Drop Binder class facade; migrate call sites to bind() function
status: Done
assignee:
  - '@claude'
created_date: '2026-05-04 13:04'
updated_date: '2026-05-04 22:03'
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
- [x] #1 Binder class removed from src/pypeeker/binder/binder.py
- [x] #2 src/pypeeker/binder/__init__.py exports bind (and BinderState if needed publicly) instead of Binder
- [x] #3 src/pypeeker/cli.py: index command uses bind() directly
- [x] #4 src/pypeeker/refactor/applier.py: _reindex_files uses bind() directly
- [x] #5 tests/conftest.py: bind_source and bind_fixture fixtures use bind() directly
- [x] #6 All 287 tests pass after migration
- [x] #7 Re-indexing pypeeker's own src/ produces identical output to TASK-17's state
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Update binder/__init__.py to export bind (and optionally BinderState) instead of Binder\n2. Update src/pypeeker/cli.py: index command uses bind() directly\n3. Update src/pypeeker/refactor/applier.py: _reindex_files uses bind() directly\n4. Update tests/conftest.py: bind_source and bind_fixture fixtures use bind() directly\n5. Remove Binder class from src/pypeeker/binder/binder.py\n6. Run full suite, re-index, verify identical output\n7. Commit, push, PR
<!-- SECTION:PLAN:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Removed the Binder class facade introduced in TASK-17 and migrated all call sites to use the module-level bind() function directly.

Changes:
- src/pypeeker/binder/binder.py: dropped Binder class (the public bind/visit_module/visit_node functions remain)
- src/pypeeker/binder/__init__.py: now exports bind, visit_module, visit_node, BinderState (Binder no longer exported)
- src/pypeeker/cli.py: index command calls bind(adapter, relative, source, tree.root_node) directly instead of Binder(adapter, relative, source).bind(tree.root_node)
- src/pypeeker/refactor/applier.py: _reindex_files migrated to bind() directly
- tests/conftest.py: bind_source / bind_fixture / indexed_project fixtures use bind() directly
- tests/test_query_engine.py: _index_source helper updated

binder.py is now 130 lines (down from 155 with the facade, originally 1187 before TASK-17). The package public surface is just the bind() function plus the Observation types.

All 287 tests pass without behavior change. Re-indexed pypeeker's own src/ and confirmed self-validation tests still produce the expected impurity findings on real code.
<!-- SECTION:FINAL_SUMMARY:END -->
