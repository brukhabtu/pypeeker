---
id: TASK-86
title: 'storage: overlay IndexStore (in-memory VFS for simulation)'
status: To Do
assignee: []
created_date: '2026-06-11 18:27'
labels:
  - storage
  - m3-planner
dependencies: []
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The composite planner simulates whole fix pipelines without touching disk. Add an overlay store: {path: bytes} layered over the real tree; reads prefer the overlay; binding runs against overlay content; indexes for overlaid files are bound in-memory. Pure bind() makes this cheap.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 OverlayIndexStore (or equivalent) supports read/write/delete of overlaid file bytes plus load/save/is_stale/list semantics consistent with IndexStore
- [ ] #2 Binding a mutated overlay file yields a correct in-memory FileIndex without disk writes; underlying store and disk remain untouched
- [ ] #3 Query engine and resolver work unchanged on an overlay store (read-through contract); tests cover layering, mutation, and isolation
<!-- AC:END -->
