---
id: TASK-126
title: 'cli: uniform mutation grammar (--plan / apply / rollback everywhere)'
status: To Do
assignee:
  - '@claude'
created_date: '2026-07-31 04:51'
labels:
  - cli
  - architecture
dependencies:
  - TASK-123
  - TASK-124
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 6: every mutating command applies by default with --plan for plan-only; apply/rollback/transactions work identically across ops; deliberate breaking CLI change with doc updates.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 rename/extract/inline/privatize/check --fix share the grammar; docs updated; full gate green.
<!-- AC:END -->
