---
id: TASK-95
title: 'check/config: library mode, public roots, dynamic-access allowlists'
status: To Do
assignee: []
created_date: '2026-06-11 18:28'
labels:
  - check
  - config
  - visibility
  - m5-visibility
dependencies: []
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Visibility and dead-code rules are dangerous for libraries: external consumers are invisible. Add project-level config: mode (app|library), public API roots (exports under these barrels are sacred), decorator allowlists (symbols carrying them count as externally called), and dynamic-access heuristics (getattr/globals proximity downgrades confidence).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Config parsed from [tool.pypeeker] via the project module; library mode exempts configured public roots from demotion/dead-code findings
- [ ] #2 Decorator allowlist suppresses unused/over-exposed findings for decorated symbols; dynamic-access proximity downgrades finding confidence
- [ ] #3 unused-public-symbol, test-only, and visibility rules consume these; tests per behavior
<!-- AC:END -->
