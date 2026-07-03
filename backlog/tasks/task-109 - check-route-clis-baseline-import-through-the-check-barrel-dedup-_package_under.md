---
id: TASK-109
title: >-
  check: route cli's baseline import through the check barrel + dedup
  _package_under
status: To Do
assignee: []
created_date: '2026-07-03 03:30'
labels:
  - architecture
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Two small cleanups from the architecture review: (1) cli.py deep-imports 5 functions from check.baseline; expose them via the check barrel and migrate cli so cli is uniformly barrel-based. (2) _package_under is duplicated in check/rules.py and check/builtin/barrel_only.py; keep one definition and import it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 cli.py imports baseline helpers via 'from pypeeker.check import ...'; check/__init__ re-exports them with __all__
- [ ] #2 _package_under has a single definition, imported by barrel_only
- [ ] #3 gate green (pytest, ruff, self-check)
<!-- AC:END -->
