---
id: TASK-53
title: 'Refactor foundation: range data-flow analysis + refactor->analysis boundary'
status: To Do
assignee: []
created_date: '2026-05-25 13:01'
labels:
  - refactor
  - analysis
  - foundation
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Reuse the analysis layer (AnalysisContext: scope subtree, local symbols, reads/writes) to answer the data-flow questions complex refactors need for a statement range inside a function: inputs (names read in the range but defined outside it), outputs (names defined in the range and read after it), whether the range has control-flow escapes (break/continue/return) and whether it is side-effect-free (purity). Add refactor->analysis to the import-boundaries allow-list (a sound downward edge).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A range data-flow helper returns inputs and outputs for a statement range within a function, built on AnalysisContext
- [ ] #2 It reports control-flow escapes (break/continue/return crossing the range boundary) and purity (side-effect-free) for the range
- [ ] #3 import-boundaries allow-list adds refactor -> analysis; pypeeker check exits 0
<!-- AC:END -->
