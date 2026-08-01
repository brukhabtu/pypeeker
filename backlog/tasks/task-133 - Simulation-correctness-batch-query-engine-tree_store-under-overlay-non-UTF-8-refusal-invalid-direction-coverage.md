---
id: TASK-133
title: >-
  Simulation correctness batch: query-engine tree_store under overlay, non-UTF-8
  refusal, invalid-direction coverage
status: In Progress
assignee: []
created_date: '2026-08-01 16:18'
updated_date: '2026-08-01 16:38'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Three small correctness items from Plan A reviews: (1) planners construct SemanticQueryEngine(index_store) with no tree_store — audit whether cross-file queries during overlay simulation consult stale real-tree state and either route the tree store through the overlay or document why it is safe; (2) extract-method on non-UTF-8 bytes raises an uncaught UnicodeDecodeError (pre-existing) — refuse via a named precondition instead; (3) the invalid-direction MaterializeError code is the one refusal class absent from the reachability matrix — add it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 All three items resolved with tests; gate green; no pre-existing test modified.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Execute via task-pipeline v2 (Opus conductor decides internals; I orchestrate outside). Item 1 has a probe-verified scout plan: the tree_store default is a confirmed unauthorized write-through — SemanticQueryEngine defaults to TreeStore(store.project_root), and under an overlay that IS the real root, so get_tree() on a simulation persists a fabricated tree.json into the real .pypeeker/ (probe-proven). Fix design: store.default_tree_store() polymorphism — IndexStore returns the disk-backed store byte-identically (a pre-existing test pins that), OverlayIndexStore returns a cached in-memory non-persisting one. Items 2 (extract-method non-UTF-8 refusal via named precondition) and 3 (invalid-direction reachability test) scouted fresh in the same run.
<!-- SECTION:PLAN:END -->
