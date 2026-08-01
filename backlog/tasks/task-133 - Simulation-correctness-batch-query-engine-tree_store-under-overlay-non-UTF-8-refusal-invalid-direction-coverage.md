---
id: TASK-133
title: >-
  Simulation correctness batch: query-engine tree_store under overlay, non-UTF-8
  refusal, invalid-direction coverage
status: To Do
assignee: []
created_date: '2026-08-01 16:18'
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
