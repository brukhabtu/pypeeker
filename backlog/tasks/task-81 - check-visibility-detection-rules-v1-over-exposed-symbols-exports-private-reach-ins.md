---
id: TASK-81
title: >-
  check: visibility-detection rules v1 (over-exposed symbols, exports, private
  reach-ins)
status: To Do
assignee: []
created_date: '2026-06-11 18:26'
labels:
  - check
  - visibility
  - m1-advisory
dependencies:
  - TASK-74
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Minimal-visibility principle, detection only: compute observed usage scope per symbol from resolved references and compare to declared visibility. Three rules: over-exposed-module-symbol (public, never referenced outside its module), over-exposed-export (barrel export no other package consumes), under-exposed-access (_private symbols referenced from outside their module, incl. tests).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 over-exposed-module-symbol flags public module-level symbols with zero cross-module references (dunder/main/dynamic-decorator allowlist exempt)
- [ ] #2 over-exposed-export flags __init__ re-exports never consumed outside the package
- [ ] #3 under-exposed-access flags cross-module references to single-underscore symbols, with test paths reported distinctly
- [ ] #4 All three opt-in with allow options; tests per rule; dogfood run over pypeeker recorded in notes
<!-- AC:END -->
