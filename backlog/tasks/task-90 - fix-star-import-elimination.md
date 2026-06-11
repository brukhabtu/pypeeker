---
id: TASK-90
title: 'fix: star-import elimination'
status: To Do
assignee: []
created_date: '2026-06-11 18:27'
labels:
  - fix
  - m4-program-fixes
dependencies:
  - TASK-84
  - TASK-89
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
from x import * leaves unresolved references only cross-module resolution can attribute. Rule detects star imports; fix resolves which names the star supplies to this module's unresolved references and rewrites an explicit import list. The ruff-cannot-do-this flagship.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Rule flags star imports; fix rewrites them to explicit sorted name lists covering every name actually used
- [ ] #2 Names the resolver cannot attribute leave the fix declined (report, no rewrite); confidence-gated
- [ ] #3 End-to-end test through plan-batch incl. a module using names from two star imports
<!-- AC:END -->
