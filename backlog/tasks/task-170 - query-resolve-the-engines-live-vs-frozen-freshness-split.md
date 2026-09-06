---
id: TASK-170
title: 'query: resolve the engine''s live-vs-frozen freshness split'
status: To Do
assignee: []
created_date: '2026-09-05 15:01'
labels:
  - architecture
dependencies:
  - TASK-157
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
SemanticQueryEngine.all_indexes re-reads the store on every call (find_symbol, references_to_binding are live) while _tree/_module_index/resolver are built once (find_importers, members are frozen). PR #140 documented the split in the class docstring and pinned it in tests/test_query_engine.py::test_symbol_queries_are_live_but_the_resolver_is_frozen, but did not resolve it. The engine needs one coherent freshness contract instead of two silently different behaviors depending on which method is called.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 One documented contract is chosen and implemented: either a full snapshot with an explicit invalidate(), or the engine is made fully live
- [ ] #2 tests/test_query_engine.py::test_symbol_queries_are_live_but_the_resolver_is_frozen is rewritten to assert the new unified contract
- [ ] #3 Every in-tree constructor of SemanticQueryEngine is audited for reliance on the old split behavior and updated as needed
- [ ] #4 No differential or self-lint regression after the change
<!-- AC:END -->
