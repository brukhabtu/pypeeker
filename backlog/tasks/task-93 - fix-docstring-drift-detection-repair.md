---
id: TASK-93
title: 'fix: docstring-drift detection + repair'
status: To Do
assignee: []
created_date: '2026-06-11 18:27'
labels:
  - fix
  - m4-program-fixes
dependencies:
  - TASK-84
  - TASK-89
priority: low
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Documented params vs actual signature drift (darglint is dead, ruff coverage shallow). Detect param-name mismatches in google/numpy/sphinx styles from symbols+docstrings; fix renames or flags stale entries. Also: rename plans update :func:/:class: docstring cross-references to renamed symbols.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Rule flags docstring params absent from the signature and signature params absent from a params section, per configurable style
- [ ] #2 Fix rewrites renameable drift (param renamed -> docstring follows); ambiguous drift is report-only
- [ ] #3 Rename planner optionally updates docstring cross-references to the renamed symbol (flag-gated); tests for both halves
<!-- AC:END -->
