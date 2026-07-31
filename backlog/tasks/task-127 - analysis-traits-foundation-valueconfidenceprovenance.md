---
id: TASK-127
title: 'analysis: traits foundation (value+confidence+provenance)'
status: To Do
assignee:
  - '@claude'
created_date: '2026-07-31 04:51'
labels:
  - analysis
  - architecture
dependencies:
  - TASK-124
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 7: introduce Trait carrying value+confidence+provenance, registered per primitive kind; migrate one rule/precondition pair (e.g. prefer-tuple's not-mutated/escapes predicate shared with NotReassigned) as proof; scattered *_confidence fields migrate over time.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Trait type + registry exist with tests; one rule and one precondition consume the same trait; full gate green.
<!-- AC:END -->
