---
id: TASK-92
title: 'fix: privatize-dead-API (demote unused/test-only public symbols)'
status: To Do
assignee: []
created_date: '2026-06-11 18:27'
labels:
  - fix
  - visibility
  - m4-program-fixes
dependencies:
  - TASK-79
  - TASK-81
  - TASK-89
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Findings from unused-public-symbol and test-only-production-code get a mechanized fix: rename name -> _name across the project, drop barrel exports/__all__ entries, transactionally via the batch planner.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Fix plans demotion renames incl. barrel/__all__ updates for unused-public and test-only findings
- [ ] #2 Symbols with heuristic-confidence findings (dynamic access nearby) are excluded from auto-fix
- [ ] #3 End-to-end batch demotion test over a fixture package; dogfood plan over pypeeker recorded (not applied) in notes
<!-- AC:END -->
