---
id: TASK-84
title: 'cli: check --fix with non-conflicting application + first three autofixes'
status: To Do
assignee: []
created_date: '2026-06-11 18:27'
labels:
  - cli
  - check
  - m2-fixes
dependencies:
  - TASK-82
  - TASK-83
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
MVP fix application without the composite planner: collect fixes from violations, drop overlapping/conflicting edit sets (byte-range overlap per file), apply the rest as one hash-verified transaction, report applied vs skipped. Prove on three easy fixes: prefer-tuple literal rewrite, unused-private-code deletion, unused-import removal.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 check --fix applies non-overlapping fixes in one transaction (preview via plan id; reuses apply/rollback machinery) and reports applied/skipped/declined
- [ ] #2 prefer-tuple, an unused-private-symbol delete fix, and an unused-import removal fix ship and are exercised end-to-end
- [ ] #3 Only confidence-certain fixes auto-apply; conflicting fixes are skipped deterministically; tests cover conflict skipping and rollback
<!-- AC:END -->
