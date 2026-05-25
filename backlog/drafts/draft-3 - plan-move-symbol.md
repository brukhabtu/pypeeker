---
id: DRAFT-3
title: plan-move-symbol
status: Draft
assignee: []
created_date: '2026-05-25 13:02'
labels: []
dependencies:
  - TASK-51
  - TASK-52
priority: low
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Move a top-level symbol (function/class) from one module to another: remove the definition from the source, insert it in the target, and update imports/usages via cross-module resolution and import-boundaries (add/remove imports, respect layering).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A symbol is relocated to the target module with its def text; importers and usages are updated; layering respected
- [ ] #2 Refuses or warns on layering violations; tests cover a cross-module move producing runnable code
<!-- AC:END -->
