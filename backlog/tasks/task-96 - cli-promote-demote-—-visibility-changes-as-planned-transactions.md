---
id: TASK-96
title: 'cli: promote/demote — visibility changes as planned transactions'
status: To Do
assignee: []
created_date: '2026-06-11 18:28'
labels:
  - cli
  - visibility
  - m5-visibility
dependencies:
  - TASK-94
  - TASK-95
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The minimal-visibility workflow needs first-class operations: demote SYMBOL plans name->_name incl. all references, barrel/__all__ updates (alias options per existing rename flags); promote plans _name->name plus export addition. Both refuse when hierarchy facts show an override contract or library-mode public roots forbid it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 pypeeker demote/promote SYMBOL_ID produce previewable transactions reusing the rename engine, incl. export handling
- [ ] #2 Hierarchy-unsafe and public-root-protected operations are refused with structured errors
- [ ] #3 CLI tests cover both directions, export updates, and refusals
<!-- AC:END -->
