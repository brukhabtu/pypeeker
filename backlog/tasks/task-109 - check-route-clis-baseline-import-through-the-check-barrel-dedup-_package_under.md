---
id: TASK-109
title: >-
  check: route cli's baseline import through the check barrel + dedup
  _package_under
status: Done
assignee: []
created_date: '2026-07-03 03:30'
updated_date: '2026-07-03 03:34'
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
- [x] #1 cli.py imports baseline helpers via 'from pypeeker.check import ...'; check/__init__ re-exports them with __all__
- [x] #2 _package_under has a single definition, imported by barrel_only
- [x] #3 gate green (pytest, ruff, self-check)
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Two small cleanups.

1. Routed cli's baseline import through the check barrel: check/__init__.py now re-exports baseline_path, clear_symbol_baseline, delta, load_baseline, write_baseline (the functions cli's baseline commands use) with __all__, and cli.py imports them via 'from pypeeker.check import ...'. cli is now uniformly barrel-based. born_private keeps its intra-package baseline import (same package, not a barrel-discipline concern).

2. Deduplicated _package_under: removed the copy in check/builtin/barrel_only.py and import the single definition from check/rules.py (barrel_only already imported register_rule from there).

1393 passed; ruff clean; self-check exit 0.
<!-- SECTION:FINAL_SUMMARY:END -->
