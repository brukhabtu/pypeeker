---
id: TASK-83
title: 'check: confidence tiers on violations'
status: To Do
assignee: []
created_date: '2026-06-11 18:26'
labels:
  - check
  - m2-fixes
dependencies:
  - TASK-74
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Linters are binary; pypeeker can label every finding with how it was resolved (declared/direct vs inferred/heuristic), the antidote to false-positive fatigue. Violation gains a confidence field; rules set it; CLI default hides low-confidence findings, --strict shows all; fix application later gates on it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Violation carries a confidence tier; existing rules label their findings (most are certain; receiver/inference-derived ones are not)
- [ ] #2 check CLI: default omits low-confidence violations, --strict includes them, output marks the tier
- [ ] #3 Sorting/format remains deterministic; tests cover tier filtering
<!-- AC:END -->
