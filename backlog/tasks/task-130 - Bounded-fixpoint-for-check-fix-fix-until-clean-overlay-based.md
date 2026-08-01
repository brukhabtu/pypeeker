---
id: TASK-130
title: 'Bounded fixpoint for check --fix (--fix-until-clean), overlay-based'
status: In Progress
assignee: []
created_date: '2026-07-31 22:25'
updated_date: '2026-08-01 02:38'
labels: []
dependencies:
  - TASK-129
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan B in roadmap-plans.md (normative — with the sequencing adjustments applied: re-based onto the overlay substrate from Plan A rather than the mirror, SIMULATION_UNSAFE_RULES re-scoped as a write-safety guard, and the cross-iteration read-through test added). Opt-in --fix-until-clean flag: re-run rules over simulated post-fix state, plan newly revealed remedies until quiescence or bound; default check --fix byte-identical; one combined transaction; stop_reason always reported.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 All Plan B acceptance criteria in roadmap-plans.md are met, including byte-identical default-path JSON, single-transaction rollback restoring pre-loop bytes, guaranteed termination with honest stop_reason, and the flagged report being a strict superset of the frozen shape.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Execute roadmap-plans.md Plan B via its workflow, WITH the ITEM B sequencing adjustments applied (they supersede the plan text): the loop runs on a persistent simulation overlay (Plan A landed — no mirror, no apply_edits_to_mirror extraction, no materialize_mirror export); SIMULATION_UNSAFE_RULES re-scoped as a write-safety guard (born_private baseline writes land on the real tree under an overlay); obsolete mirror risks/tests deleted; the cross-iteration read-through test added (a remedy planned in iteration 2 must see iteration 1 bytes through the per-call overlay nesting). Opt-in --fix-until-clean; default check --fix byte-identical; one combined transaction; stop_reason always honest.
<!-- SECTION:PLAN:END -->
