---
id: TASK-89
title: 'refactor/cli: flattened batch transactions + plan-batch command'
status: To Do
assignee: []
created_date: '2026-06-11 18:27'
labels:
  - refactor
  - cli
  - m3-planner
dependencies:
  - TASK-88
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
After simulation, diff overlay content against original disk per file and emit ONE ordinary transaction (hash-anchored to plan-time files, old=original content for rollback) so apply/rollback reuse the existing applier unchanged. CLI: plan-batch consumes a set of intents (from fix-emitting rules and/or an intent file), prints the plan summary incl. dropped intents; apply/rollback work as today.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Simulation output flattens to a single transaction applying atomically via the existing applier, with rollback restoring originals byte-identically
- [ ] #2 plan-batch CLI plans multi-intent batches (e.g. all check --fix fixes plus explicit renames) and reports executed/dropped intents with reasons
- [ ] #3 End-to-end test: a batch mixing a rename, an inline, and a delete that would corrupt under naive sequential transactions lands correctly
<!-- AC:END -->
