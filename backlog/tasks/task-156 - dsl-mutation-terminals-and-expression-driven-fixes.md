---
id: TASK-156
title: 'dsl: mutation terminals and expression-driven fixes'
status: To Do
assignee: []
created_date: '2026-08-03 18:11'
labels: []
dependencies:
  - TASK-153
  - TASK-154
  - TASK-155
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 4 of the DSL rewrite (dsl-rewrite.md is normative). Mutation values: named top-level objects carrying kind, params, preconditions, and the confidence floor; an application operator taking no options that yields intents into the existing batch machinery; demote and privatize as two selections over ONE shared mutation value; evidence-typed anchors making the CLI-typed-id-is-DECLARED rule real; check --fix driven through the new engine behind the differential gate; v1 composites resolve to existing planner kinds only, erroring at construction.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 demote and privatize share one mutation value; the confidence floor is an attribute of that value; a CLI-typed id applies where a heuristic finding refuses
- [ ] #2 Application yields flat unordered intents consumed by the existing batch scheduler unchanged
- [ ] #3 check --fix through the new engine is differentially identical to the old path or ledgered
- [ ] #4 Full gate green
<!-- AC:END -->
