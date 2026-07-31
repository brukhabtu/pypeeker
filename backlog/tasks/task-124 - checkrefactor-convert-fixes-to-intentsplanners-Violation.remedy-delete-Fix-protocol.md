---
id: TASK-124
title: >-
  check+refactor: convert fixes to intents+planners; Violation.remedy; delete
  Fix protocol
status: To Do
assignee:
  - '@claude'
created_date: '2026-07-31 04:51'
updated_date: '2026-07-31 04:53'
labels:
  - check
  - refactor
  - architecture
dependencies:
  - TASK-122
  - TASK-123
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 4: each of the five fixes becomes a refactor planner with a real intent (delete-symbol, remove-import, rewrite-star-import, tuplify, docstring-param via ReplaceTextIntent). Violation.fix becomes Violation.remedy: Intent|None. Delete check/fixes.py, check/protocols.py, FixIntent. check may import intents (leaf) but still never refactor.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Five planners exist in refactor/ with real footprints/effects; rules attach remedy intents; check --fix routes violations' remedies through the batch engine.
- [ ] #2 Fix, FixPlan, FixDeclined, DeclineReason, FixIntent are deleted; no fix-protocol code remains; full gate green.
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Also introduces the Anchor union (SymbolAnchor | RangeAnchor | EdgeAnchor) in intents/, deferred from phase 1 because an export with no src consumer trips the unused-public-symbol gate; Violation.remedy is its first consumer.
<!-- SECTION:NOTES:END -->
