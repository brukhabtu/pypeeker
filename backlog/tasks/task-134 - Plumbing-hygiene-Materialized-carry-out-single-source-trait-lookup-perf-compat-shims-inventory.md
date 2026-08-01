---
id: TASK-134
title: >-
  Plumbing hygiene: Materialized carry-out single source, trait lookup perf,
  compat shims inventory
status: To Do
assignee: []
created_date: '2026-08-01 16:18'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Debt noted across reviews, none behavior-affecting: (1) submit_intent rebuilds Materialized from ExecutedIntent by a hand-copied field list — three unlinked copies of the shape; give the carry-out one constructor/helper so a new field cannot be silently dropped (the files_created drop in D-PR3 was exactly this failure mode); (2) the type-annotation trait provider re-resolves symbols by linear scan and prefer_tuple pays O(candidates x symbols) — index by symbol_id; (3) inventory the LoadedTransaction/_FlattenedTransaction tuple-compat shims and either schedule destructure-site migration or record the shims as permanent; (4) the trait conformance test identifies builtin providers by module-prefix heuristic — make it explicit.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Carry-out has a single construction path with a test proving a new field cannot be dropped silently; trait lookups indexed; shim decision recorded; gate green.
<!-- AC:END -->
