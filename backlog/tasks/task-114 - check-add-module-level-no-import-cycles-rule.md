---
id: TASK-114
title: 'check: add module-level no-import-cycles rule'
status: Done
assignee:
  - '@claude'
created_date: '2026-07-30 22:44'
updated_date: '2026-07-30 22:55'
labels:
  - check
  - refactor
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
import-boundaries enforces an acyclic PACKAGE DAG but is blind to cycles between MODULES within a package (e.g. the models<->fixes cycle that was hidden under TYPE_CHECKING, now fixed). Add a project-scoped no-import-cycles rule that builds the module-level import graph from IMPORT symbols (including TYPE_CHECKING edges, which the binder already recovers) and reports strongly-connected cycles. This closes the blind spot and makes the tool enforce its own acyclicity. Include an allow option as an escape hatch for accepted cycles.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A project-scoped 'no-import-cycles' rule builds a directed module graph from module-load-time IMPORT symbols mapped to their origin project module, and reports each import cycle (strongly-connected component of size >= 2) as a violation naming the modules in the cycle.
- [x] #2 TYPE_CHECKING-guarded edges are included (guard-hidden cycles are caught); function-local (deferred) imports are excluded (they run at call time and form no module-load cycle); external/stdlib modules are excluded; a configurable 'allow' list can exempt specific accepted cycles.
- [x] #3 The rule reports zero cycles on pypeeker's own source and is enabled in [tool.pypeeker].rules as a gate; unit tests cover a 2-module cycle, a 3-module cycle, a TYPE_CHECKING-hidden cycle, a function-local deferred import (no finding), an acyclic graph (no findings), external imports (no findings), and the allow exemption.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add project-scoped no-import-cycles rule: build module graph from load-time IMPORT symbols, resolve origin via CrossModuleResolver, Tarjan SCC, allow escape hatch.
2. Exclude deferred function-local imports (walk scope chain; skip if any function/lambda/comprehension scope) so idiomatic recursion-breaking imports are not flagged.
3. Enable in pyproject rules; verify self-lint is clean.
4. Unit tests for 2/3-module cycles, TYPE_CHECKING-hidden, deferred import, acyclic, external, allow.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented src/pypeeker/check/builtin/no_import_cycles.py (project scope). Origin resolved through resolver.resolve_definition + module_of; iterative Tarjan SCC; allow parses list-of-lists into frozenset member sets.

Key finding: enabling the rule surfaced a real 4-module SCC in the binder (assignments/binder/references/scopes). It was NOT a load-time cycle — the visitors import the visit_node dispatcher inside function bodies (idiomatic mutual recursion). Refined the rule to count only module-load-time imports (module/class body) via _runs_at_import_time, which walks the enclosing scope chain and defers on any function/lambda/comprehension scope. TYPE_CHECKING blocks still count (they execute at import time). After the refinement the self-lint is clean (check exit 0).

Gate: 1398 pytest passed, ruff clean, pypeeker check exit 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Add a project-scoped `no-import-cycles` check rule that catches module-level import cycles inside a package — the blind spot left by `import-boundaries`, which only enforces an acyclic *package* DAG.

## What changed
- New `check/builtin/no_import_cycles.py`: builds the directed module graph from IMPORT symbols, charging each import to its *origin* module (resolved through re-export chains by the shared `CrossModuleResolver`, matching `import-boundaries`). Cycles are found with an iterative Tarjan SCC pass; every SCC of >= 2 modules is reported, naming its members. An `allow` option (list of module-name lists) is an escape hatch for deliberately-kept cycles.
- Only *module-load-time* imports count. `TYPE_CHECKING`-guarded imports are included (they execute at import time and the binder recovers them), but *function-local* deferred imports are excluded via `_runs_at_import_time`, which walks the enclosing scope chain and treats any function/lambda/comprehension scope as deferred. This is what lets idiomatic mutual recursion (e.g. the binder visitors importing the `visit_node` dispatcher inside their bodies) coexist with the rule.
- Enabled in `[tool.pypeeker].rules` as a self-lint gate.

## Why the scope refinement
Enabling the rule surfaced a 4-module SCC in the `binder` package (assignments/binder/references/scopes). It was not a real module-load cycle: the back-edges to `binder` are function-body imports of `visit_node`, the standard way to express cross-module recursion without a load-time cycle. Counting only load-time imports both eliminates that false positive and keeps the rule faithful to what an "import cycle" means for initialization order.

## Tests
`tests/test_rule_no_import_cycles.py`: 2-module cycle, 3-module cycle, TYPE_CHECKING-hidden cycle, function-local deferred import (no finding), acyclic graph (no finding), external/stdlib imports (no finding), and the `allow` exemption. Full gate: 1398 pytest passed, ruff clean, `pypeeker check` exit 0.
<!-- SECTION:FINAL_SUMMARY:END -->
