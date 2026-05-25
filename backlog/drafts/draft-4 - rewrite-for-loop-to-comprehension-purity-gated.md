---
id: DRAFT-4
title: rewrite for-loop to comprehension (purity-gated)
status: Draft
assignee: []
created_date: '2026-05-25 13:02'
labels: []
dependencies:
  - TASK-52
  - TASK-53
priority: low
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
CST-shape detection of the empty-collection-init + accumulating-for-loop pattern, rewritten to a comprehension - but only when the loop body is side-effect-free (purity) and has no break/continue/early return. The purity gate is the differentiator over a purely syntactic linter (ruff PERF401).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Detects init+append/add/setitem loop shapes and offers a comprehension rewrite only when the body is pure and escape-free
- [ ] #2 Suggestion is correct end-to-end on safe cases; unsafe cases are skipped; tests cover both
<!-- AC:END -->
