---
id: TASK-74
title: 'check: auto-discovered builtin rule modules'
status: To Do
assignee: []
created_date: '2026-06-11 18:25'
labels:
  - check
  - m1-advisory
dependencies: []
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
rules.py is a monolith; parallel rule development needs drop-in modules. Add src/pypeeker/check/builtin/ whose __init__ auto-imports submodules (pkgutil), each self-registering via the existing register_rule decorator. Engine imports the package once. Unblocks every M1 rule landing as its own file with zero shared-file edits.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A rule module dropped into check/builtin/ is discovered and runnable by name with no registry edits
- [ ] #2 Existing REGISTRY built-ins and plugin path unchanged; name-clash precedence documented
- [ ] #3 Unit test covers discovery; full suite green
<!-- AC:END -->
