---
id: TASK-130
title: 'Bounded fixpoint for check --fix (--fix-until-clean), overlay-based'
status: To Do
assignee: []
created_date: '2026-07-31 22:25'
updated_date: '2026-07-31 22:26'
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
