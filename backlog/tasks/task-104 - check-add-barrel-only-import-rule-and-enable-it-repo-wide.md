---
id: TASK-104
title: 'check: add barrel-only import rule and enable it repo-wide'
status: To Do
assignee: []
created_date: '2026-07-03 01:57'
labels:
  - architecture
  - check
dependencies:
  - TASK-103
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Consumers can bypass a package's curated public surface by deep-importing its internal submodules (from pypeeker.refactor.planner import X instead of from pypeeker.refactor import X). Now that query/refactor/models barrels exist, add an opt-in 'barrel-only' rule to pypeeker check that flags a cross-package import of an internal submodule when the target package exposes a curated barrel, and enable it on this repo. Complements import-boundaries: that rule governs which packages may depend on which; this one governs that they depend via the public surface.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 New rule flags cross-package imports of a package's internal submodules when that package's __init__ defines __all__; same-package and barrel imports are never flagged
- [ ] #2 Rule is opt-in via [tool.pypeeker] rules and has unit tests covering: deep import flagged, barrel import clean, same-package deep import clean, package without a curated barrel not flagged
- [ ] #3 Enabled in this repo's pyproject and the self-check (index src && check) passes
<!-- AC:END -->
