---
id: TASK-67
title: 'Expose purity: pypeeker purity command (and rename is_pure to impurities)'
status: To Do
assignee: []
created_date: '2026-06-11 15:47'
labels:
  - analysis
  - cli
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The largest, best-tested analysis in the codebase (is_pure, call graph, typed receivers) has no CLI command and no check rule — only tests and (partially) refactor/dataflow consume it. Also, is_pure returns truthy-for-impure, a trap its own docstring acknowledges. Expose it and fix the name in the same change.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 pypeeker purity <symbol-id> emits a JSON verdict plus the typed observations (kind, line, detail) including transitive impure calls
- [ ] #2 is_pure is renamed to a name matching its semantics (e.g. impurities); all call sites and tests updated
- [ ] #3 Unanalyzable symbols produce a structured error (not-found / not-a-function), exit non-zero
<!-- AC:END -->
