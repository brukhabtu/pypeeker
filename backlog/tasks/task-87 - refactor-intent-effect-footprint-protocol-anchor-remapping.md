---
id: TASK-87
title: 'refactor: intent/effect/footprint protocol + anchor remapping'
status: To Do
assignee: []
created_date: '2026-06-11 18:27'
labels:
  - refactor
  - m3-planner
dependencies:
  - TASK-85
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Composite plans are lists of intents (transform + semantic anchor + options), not byte edits. Each transform declares reads (symbols, files, derived facts) and effects (files written; ids created/deleted/renamed with mappings). Renames produce id substitutions applied to downstream intents' anchors. Conflict = write/write or write/read footprint intersection.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Intent, Effect, and Footprint types exist; rename/inline/extract/delete-style transforms can be expressed as intents with declared footprints and effects
- [ ] #2 Anchor remapping rewrites pending intents through rename/delete effects (delete orphans dependents with a reported reason)
- [ ] #3 Conflict detection between two intents is a pure function with tests covering rename-vs-edit, delete-vs-rename, and disjoint cases
<!-- AC:END -->
