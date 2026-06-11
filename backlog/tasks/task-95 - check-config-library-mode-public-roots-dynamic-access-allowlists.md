---
id: TASK-95
title: 'check/config: library mode, public roots, dynamic-access allowlists'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-06-11 18:28'
updated_date: '2026-06-11 19:07'
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

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add VisibilityConfig frozen dataclass + load_visibility_config to project.py (owns [tool.pypeeker.visibility] parsing)
2. CheckConfig gains visibility field; load_config injects parsed section into every rule options dict under reserved key "visibility"
3. Consume in unused-public-symbol, over-exposed-module-symbol, over-exposed-export, test-only-production-code: library-mode public-roots exemption via existing barrel detection; merged global allow-decorators; decorator exemption added to unused-public-symbol and test-only
4. Dynamic-access proximity: builtin getattr/globals/vars/locals refs in defining module append low-confidence suffix to messages
5. Tests per behavior + regression defaults; ruff + pytest green
<!-- SECTION:PLAN:END -->
