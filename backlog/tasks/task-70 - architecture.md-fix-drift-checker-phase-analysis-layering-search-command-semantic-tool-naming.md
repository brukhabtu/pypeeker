---
id: TASK-70
title: >-
  architecture.md: fix drift (checker phase, analysis layering, search command,
  semantic-tool naming)
status: To Do
assignee: []
created_date: '2026-06-11 15:47'
labels:
  - docs
dependencies: []
priority: low
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
architecture.md is load-bearing, so drift matters: the pipeline diagram shows a Checker (type info) phase that does not exist; the layering list omits resolve from analysis deps while pyproject allows it; 'search <query>' is documented as a core command but unimplemented; docs and the storage dir still say semantic-tool while the product is pypeeker. Decide and record the naming question; align the rest.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Pipeline diagram matches the implemented phases
- [ ] #2 Module layering section matches pyproject [tool.pypeeker.import-boundaries.allow]
- [ ] #3 Unimplemented commands (search) are marked roadmap or removed
- [ ] #4 semantic-tool vs pypeeker naming decision recorded (doc note or decision record); no silent inconsistency
<!-- AC:END -->
