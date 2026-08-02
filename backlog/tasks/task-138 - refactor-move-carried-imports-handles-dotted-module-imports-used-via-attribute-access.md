---
id: TASK-138
title: >-
  refactor/move: carried-imports handles dotted module imports used via
  attribute access
status: Done
assignee:
  - '@claude'
created_date: '2026-08-02 01:29'
updated_date: '2026-08-02 06:36'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The carried-imports analysis behind move-symbol (preconditions.py CarriedImportsUnconditional and the move planner) misses the dotted-module form: import os.path binds the name os, and a moved body using os.path.join reaches the submodule purely through attribute access, so the dependency is not detected as needing to be carried or refused. Scout must characterize the exact miss and its blast radius before implementation.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Moving a symbol whose body uses a dotted-module import via attribute access either carries the import correctly or refuses with a named precondition — never silently drops the dependency
- [x] #2 Regression tests cover the import os.path attribute-access pattern for both the carry and refusal paths
- [x] #3 Full gate green (pytest, ruff, self-lint)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v3 on the working branch (sole mutating run); scout must characterize the exact miss before implementation; orchestrator gates, ships PR, bookkeeps.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Pipeline run wf_dd000e4f-317 (14 agents). Scout probe matrix (9 import shapes, real CLI + python3 execution of moved output) confirmed the hypothesis and found it worse: dotted forms bypassed CarriedImportsUnconditional entirely (TYPE_CHECKING-guarded dotted import silently dropped, exit 0); local-package case NameErrors at runtime.
- Root cause: binder declares import a.b as one symbol NAMED a.b while the body refs bare root a — symbol_id lookup never matched.
- Fix: two-tier spelled-path attribution in MovedBodyClosed + binder-blind-binding scan (re-review caught match/case captures, PEP 695 type params, unpacking as-targets masquerading as roots).
- No frozen-test collisions; 1115 added test lines, zero modified.
- Shipped as PR #111; gate green (2047 tests).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Carried-imports now detects dotted module imports used via attribute access; shipped as PR #111 (squash-merged).

- Two-tier spelled-path attribution (prefix + root-fallback) in preconditions.py; guarded dotted imports now hit the existing carried-imports-unconditional fail-closed refusal instead of silently dropping.
- Binder-blind-binding scan prevents mis-attribution from match captures, PEP 695 type params, unpacking as-targets.
- Aliased/from/plain forms probe-verified correct before and after; quoting-invariance preserved.
- 1115 added test lines incl. local dotted packages on both carry and refusal paths.

Tests: full gate green (2047 passed, ruff, self-lint zero findings).
<!-- SECTION:FINAL_SUMMARY:END -->
