---
id: TASK-94
title: 'analysis: class hierarchy facts (bases, overrides)'
status: To Do
assignee: []
created_date: '2026-06-11 18:28'
labels:
  - analysis
  - m5-visibility
dependencies: []
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The model binds superclass references but builds no hierarchy: privatizing or renaming a method that overrides a base / implements a Protocol / is overridden breaks contracts invisibly. Add hierarchy facts: per class, resolved base ids (via CrossModuleResolver); per method, overrides/overridden-by. Rename gains a safety check; visibility demotion requires it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Hierarchy query resolves base classes cross-module (unresolvable/external bases marked unknown) and computes overrides/overridden-by per method
- [ ] #2 Rename planner warns or refuses (flag-gated) when renaming only one side of an override pair
- [ ] #3 Tests cover single inheritance, cross-module bases, Protocol implementation, and unknown external bases
<!-- AC:END -->
