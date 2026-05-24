---
id: TASK-40
title: 'Performance: cache FileIndex loads in IndexStore'
status: Done
assignee:
  - '@claude'
created_date: '2026-05-24 03:42'
updated_date: '2026-05-24 03:43'
labels:
  - index
  - performance
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Dogfooding-found performance issue. is_pure(store, sid) is O(n^2)-ish: it rebuilds call_graph and calls AnalysisContext.for_function for the target plus every reachable function; for_function spins up a fresh SemanticQueryEngine and resolves via find_symbol (a full index load), and IndexStore.load re-reads and re-parses the JSON every call. The purity survey took 319s for 192 functions. Root cause is repeated FileIndex loading/parsing. Fix: memoize IndexStore.load in-process, invalidated on save()/remove(), so call_graph, every for_function engine, and all sub-contexts share parsed indexes.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 IndexStore.load caches parsed FileIndex objects in-process; repeated load(path) does not re-read or re-parse
- [x] #2 save() and remove() keep the cache consistent (save updates, remove evicts); is_stale and reindex-after-edit remain correct
- [x] #3 A representative purity survey over src/ runs dramatically faster than before (order-of-magnitude); full suite green; pypeeker check exits 0
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
index_store.py: add self._cache dict; load() returns/fills cache; save() sets cache[path]=file_index; remove() pops. Verify storage tests + reindex flow. Re-time the purity survey. suite+check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
index_store.py: added an in-process dict cache to IndexStore. load() returns/fills it; save() updates it; remove() evicts it. is_stale still compares the cached index file_hash against the on-disk source hash, so staleness/reindex remain correct.

Measured impact: the purity survey over src/ dropped 319s -> 2.6s (~120x). The full test suite dropped ~26s -> 1.8s (analysis/purity tests were re-parsing indexes on every load). 428 tests pass; pypeeker check exits 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Cache parsed FileIndex objects in IndexStore to fix a dogfooding-found performance problem. is_pure rebuilds the call graph and calls AnalysisContext.for_function for the target plus every reachable function; for_function spins up a fresh SemanticQueryEngine that resolves via a full index load, and IndexStore.load previously re-read and re-parsed JSON on every call — so analysis was dominated by redundant parsing.

IndexStore now memoizes load() in-process, invalidated by save() (updates) and remove() (evicts); is_stale and reindex-after-edit stay correct because staleness compares the cached index hash to the on-disk source hash.

Impact: the purity survey over src/ went 319s -> 2.6s (~120x), and the full test suite went ~26s -> 1.8s (the analysis-heavy tests were re-parsing indexes repeatedly). 428 tests pass; pypeeker check exits 0.
<!-- SECTION:FINAL_SUMMARY:END -->
