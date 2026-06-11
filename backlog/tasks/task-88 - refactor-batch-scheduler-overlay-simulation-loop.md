---
id: TASK-88
title: 'refactor: batch scheduler + overlay simulation loop'
status: To Do
assignee: []
created_date: '2026-06-11 18:27'
labels:
  - refactor
  - m3-planner
dependencies:
  - TASK-86
  - TASK-87
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The core engine: order intents (explicit deps + footprint conflicts; id-changing ops late, deletes after readers, deterministic tie-break), then execute sequentially against the overlay — materialize edits, mutate overlay, incrementally re-bind touched files, remap anchors, re-validate each intent's preconditions at its turn. Stale intents drop with reasons (skip-and-report) or abort (all-or-nothing), per policy. Fixpoint-capped.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Scheduler produces a deterministic order from deps + footprints; cycles and hard conflicts are reported, not silently resolved
- [ ] #2 Simulation applies intents on the overlay with guarded re-validation; dropped intents carry machine-readable reasons
- [ ] #3 skip-and-report and all-or-nothing policies both work; iteration is bounded; tests cover interfering renames, inline-then-delete-import chains, and a stale-guard drop
<!-- AC:END -->
