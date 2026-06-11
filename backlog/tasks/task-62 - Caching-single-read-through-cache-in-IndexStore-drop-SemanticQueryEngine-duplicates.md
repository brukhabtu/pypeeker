---
id: TASK-62
title: >-
  Caching: single read-through cache in IndexStore; drop SemanticQueryEngine
  duplicates
status: To Do
assignee: []
created_date: '2026-06-11 15:46'
labels:
  - query
  - storage
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Three uncoordinated cache layers exist: IndexStore._cache, SemanticQueryEngine._loaded_indexes (plus _tree/_module_index), and CrossModuleResolver's constructor snapshot. The engine cache became redundant when IndexStore grew its cache (task-40). Make IndexStore the single cache; engine reads through it; document or coherently invalidate the resolver/tree snapshots.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 SemanticQueryEngine._loaded_indexes is removed; the engine reads through IndexStore
- [ ] #2 Resolver/tree/module-index lifetimes are documented as engine-lifetime snapshots or invalidated coherently on store.save/remove
- [ ] #3 Full test suite passes
<!-- AC:END -->
