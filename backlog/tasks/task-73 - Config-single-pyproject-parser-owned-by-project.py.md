---
id: TASK-73
title: 'Config: single pyproject parser owned by project.py'
status: To Do
assignee: []
created_date: '2026-06-11 15:47'
labels:
  - config
  - clarity
dependencies: []
priority: low
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
project.py and check/config.py both parse [tool.pypeeker] and both define a ('src',) default. One config module should own pyproject access; check should consume it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 One module owns [tool.pypeeker] parsing; check/config.py consumes it
- [ ] #2 Single source of truth for the default src roots
- [ ] #3 Full test suite passes
<!-- AC:END -->
