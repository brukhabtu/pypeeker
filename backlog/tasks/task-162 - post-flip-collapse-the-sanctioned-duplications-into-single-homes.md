---
id: TASK-162
title: 'post-flip: collapse the sanctioned duplications into single homes'
status: To Do
assignee: []
created_date: '2026-08-08 19:59'
labels:
  - dsl
  - cleanup
dependencies:
  - TASK-157
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
dsl deliberately re-implements slices of old-engine code (config reading in dsl/differential.py and dsl/visibility.py, _enum_set copying check's _as_enum_set, _storage_root duplicating storage.index_store.resolve_storage_root) because the new engine may not execute old-engine code while the differential oracle is live. That rationale expires when TASK-157 deletes check/. Then: export resolve_storage_root from the storage barrel and use it; promote the shared config slice into project/ (or a config leaf) with one implementation; delete the dsl-local copies. Blocked on the flip by design — doing it earlier breaks oracle independence.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Each formerly-duplicated slice has exactly one implementation in a proper home, and dsl imports it through barrels
- [ ] #2 Full gate green
<!-- AC:END -->
