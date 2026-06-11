---
id: TASK-75
title: 'check: no-argument-mutation rule (advisory)'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-06-11 18:25'
updated_date: '2026-06-11 18:34'
labels:
  - check
  - analysis
  - m1-advisory
dependencies:
  - TASK-74
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Functions that mutate their parameters have caller-visible side effects no linter can see. Pypeeker already classifies receiver kinds: parameter-receiver collection mutations, attribute writes, and subscript writes on parameters are detectable today. Flag them; self/cls excluded; configurable allowlist (e.g. methods documented as mutating).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Rule flags collection-mutator calls, attribute writes, and subscript writes whose receiver root is a PARAMETER (self/cls excluded)
- [ ] #2 Options: allow (function-id patterns), extra mutator names; opt-in rule
- [ ] #3 Tests cover each mutation shape plus non-flagged local/self cases; dogfood run on pypeeker recorded in notes
<!-- AC:END -->
