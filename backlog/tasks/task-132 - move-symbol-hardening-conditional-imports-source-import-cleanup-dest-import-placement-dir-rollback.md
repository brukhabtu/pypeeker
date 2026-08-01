---
id: TASK-132
title: >-
  move-symbol hardening: conditional imports, source import cleanup, dest import
  placement, dir rollback
status: To Do
assignee: []
created_date: '2026-08-01 16:17'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Advisories from the D-PR3 adversarial review (all currently correct-but-rough or refuse-too-late): (1) a TYPE_CHECKING-guarded or otherwise conditional import used by the moved body is carried into the destination unguarded — should be refused by name or carried with its guard; (2) import bindings in the source module used only by the moved definition are left dangling (unused-import debt the move itself creates); (3) imports appended to an existing destination land above the appended def mid-file (E402-style) instead of joining the top import block; (4) rolling back a move that created a module in a new directory deletes the file but leaves the empty directory; (5) plan-batch help text does not list the move-symbol kind. Also: the back-imports follow-up (source module importing the moved symbol when remaining code uses it — currently refused by source-module-free).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Each numbered advisory either fixed with behavior-pinning tests or explicitly recorded as accepted behavior in architecture.md; gate green; no pre-existing test modified.
<!-- AC:END -->
