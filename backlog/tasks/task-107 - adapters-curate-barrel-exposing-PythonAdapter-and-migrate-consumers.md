---
id: TASK-107
title: 'adapters: curate barrel exposing PythonAdapter and migrate consumers'
status: Done
assignee:
  - '@claude'
created_date: '2026-07-03 03:06'
updated_date: '2026-07-03 03:07'
labels:
  - architecture
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
adapters is the only package without a curated __all__ barrel; 8 consumers deep-import PythonAdapter from adapters.python_adapter. Add a curated barrel and migrate them so adapters is consistent with the other packages and covered by the barrel-only rule.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 adapters/__init__.py re-exports PythonAdapter with __all__ (keeping its existing explanatory docstring)
- [x] #2 The 8 in-repo PythonAdapter deep imports use the barrel; self-check with barrel-only passes
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added a curated barrel to the adapters package (the only package that lacked one). adapters/__init__.py keeps its explanatory docstring and now re-exports PythonAdapter with __all__; migrated all 8 in-repo deep imports (cli, indexer, binder, refactor) to 'from pypeeker.adapters import PythonAdapter'. adapters is now consistent with the other 8 curated packages and covered by the barrel-only rule. 1391 passed; ruff clean; self-check exit 0.
<!-- SECTION:FINAL_SUMMARY:END -->
