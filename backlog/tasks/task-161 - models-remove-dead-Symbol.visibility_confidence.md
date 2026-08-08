---
id: TASK-161
title: 'models: remove dead Symbol.visibility_confidence'
status: To Do
assignee: []
created_date: '2026-08-08 19:59'
labels:
  - models
  - binder
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-149 established the field is dead: HEURISTIC on 9,787/9,787 symbols, gates nothing anywhere. Grep confirms no frozen path reads it (models + binder only), so removal is safe pre-flip. Remove the field from Symbol, its assignments in binder, and any serialization; indexes are regenerated locally so no migration. If the scout finds a live consumer after all, stop and report instead of forcing.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Symbol.visibility_confidence is gone and nothing references it
- [ ] #2 Full gate green
<!-- AC:END -->
