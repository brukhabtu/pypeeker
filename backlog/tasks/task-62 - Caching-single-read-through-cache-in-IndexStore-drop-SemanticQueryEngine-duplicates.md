---
id: TASK-62
title: >-
  Caching: single read-through cache in IndexStore; drop SemanticQueryEngine
  duplicates
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 15:46'
updated_date: '2026-06-11 16:12'
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
- [x] #1 SemanticQueryEngine._loaded_indexes is removed; the engine reads through IndexStore
- [x] #2 Resolver/tree/module-index lifetimes are documented as engine-lifetime snapshots or invalidated coherently on store.save/remove
- [x] #3 Full test suite passes
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Remove _loaded_indexes and _load_index from SemanticQueryEngine; _load_all_indexes and get_scope_at read through self._store.load() directly (IndexStore caches and invalidates on save/remove).
2. Document on SemanticQueryEngine that _tree/_module_index/_resolver are engine-lifetime snapshots: queries consistent as of first load; new engine picks up index changes. Note cli.py refreshes stale indexes before engine construction (verified), so snapshot lifetime matches command lifetime.
3. Verified call paths (cli.py, refactor/planner.py, refactor/inline.py, analysis/context.py): no path saves an index then expects the same engine instance to observe it.
4. Add a note in IndexStore docstring that it is the single read-through cache.
5. Add test pinning that engine reads reflect IndexStore.save() through the same store; run full pytest.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Removed SemanticQueryEngine._loaded_indexes and _load_index; _load_all_indexes and get_scope_at now call self._store.load() directly (IndexStore is the single read-through cache, invalidated by save()/remove()).
- Documented the caching/freshness contract on the SemanticQueryEngine class docstring: engine instance is a snapshot view; _tree/_module_index/_resolver are engine-lifetime memoizations built lazily at first use; construct a new engine to pick up index changes. Verified in cli.py that _refresh_index runs before engine construction in every query command, so snapshot lifetime matches command lifetime.
- Added a note in IndexStore.__init__ that its _cache is the single read-through cache and callers should not keep per-file caches.
- Audited call paths for stale-snapshot exposure: cli.py constructs engines per command after refresh; refactor/planner.py and refactor/inline.py build engines in __init__ and only write to TransactionStore (never IndexStore); analysis/context.py builds a fresh engine per for_function call. No path saves a FileIndex and expects the same engine instance to see it via derived structures.
- Added test_engine_reads_reflect_store_save_through_same_store pinning that per-file engine reads observe IndexStore.save() made through the same store.
- Full suite: 568 passed, 10 skipped, 0 failures (uv run pytest -q).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Consolidated FileIndex caching into a single read-through cache in IndexStore and removed the redundant duplicate cache in SemanticQueryEngine.

Changes:
- src/pypeeker/query/engine.py: removed `_loaded_indexes` and `_load_index`; `_load_all_indexes` and `get_scope_at` now read through `IndexStore.load()` directly, which caches in-process and invalidates on `save()`/`remove()`. Behavioral improvement: per-file engine reads now observe writes made through the same store, instead of serving never-invalidated engine-local copies.
- Documented the caching/freshness contract on the engine class: an engine instance is a snapshot view — the derived memoizations (`_tree`, `_module_index`, `_resolver`) are built lazily at first use and not invalidated afterwards; construct a new engine to pick up index changes. Noted (and verified in cli.py) that `_refresh_index` re-indexes stale files before engine construction, so snapshot lifetime matches command lifetime.
- src/pypeeker/storage/index_store.py: docstring note that `_cache` is the single read-through cache for FileIndex objects; callers must not keep per-file caches of their own.

Stale-snapshot audit: cli.py builds engines per command after refresh; refactor/planner.py and refactor/inline.py build engines in `__init__` and write only to TransactionStore; analysis/context.py builds a fresh engine per call. No call path saves an index and expects the same engine instance to observe it via derived structures.

Tests:
- New test_engine_reads_reflect_store_save_through_same_store in tests/test_query_engine.py pins that engine reads reflect IndexStore.save() through the same store (find_symbol and get_scope_at).
- Full suite: 568 passed, 10 skipped, 0 failures.
<!-- SECTION:FINAL_SUMMARY:END -->
