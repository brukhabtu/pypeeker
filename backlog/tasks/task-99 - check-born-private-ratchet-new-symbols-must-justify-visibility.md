---
id: TASK-99
title: 'check: born-private ratchet (new symbols must justify visibility)'
status: To Do
assignee: []
created_date: '2026-06-11 18:28'
labels:
  - check
  - visibility
  - m6-ratchets
dependencies:
  - TASK-81
  - TASK-95
  - TASK-98
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Enforce 'private until needed' prospectively: flag newly-public symbols (vs baseline) whose observed usage scope does not justify public visibility — without relitigating legacy code.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Rule flags public symbols absent from the baseline whose references are all module-local (per visibility-detection scope computation)
- [ ] #2 Respects library-mode public roots and decorator allowlists; opt-in
- [ ] #3 Tests cover new-over-exposed flagged, new-justified-public passing, legacy untouched
<!-- AC:END -->
