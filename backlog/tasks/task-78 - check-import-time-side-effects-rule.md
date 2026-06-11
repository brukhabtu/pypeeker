---
id: TASK-78
title: 'check: import-time-side-effects rule'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 18:25'
updated_date: '2026-06-11 18:43'
labels:
  - check
  - analysis
  - m1-advisory
dependencies:
  - TASK-74
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Imports should be free. Module-scope CALL references that hit the purity policy (I/O, network, time, subprocess) or call project functions that are impure make importing the module a side effect. Detectable from in_scope_id == module scope plus the purity policy and call graph.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Rule flags module-scope calls matching the impure builtin/module policy, and module-scope calls to project functions whose impurities() is non-empty
- [x] #2 Options: allow patterns (e.g. logging.getLogger), extra-impure; opt-in
- [x] #3 Tests cover direct impure call, impure project-function call, and allowed patterns; dogfood run recorded
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add src/pypeeker/check/builtin/import_time_side_effects.py registering a project-scoped rule via register_rule (auto-discovered)
2. Detect import-time scopes per FileIndex: module scope plus CLASS/COMPREHENSION scopes whose ancestors are all import-time (class bodies execute at import)
3. Classify CALL refs in those scopes: (a) bare/builtin names vs policy.impure_builtins, (b) IMPORT-rooted qualified calls (imported_from + chain[1:] + leaf) vs policy.module_impure_names, (c) refs resolving to project FUNCTION/METHOD symbols with non-empty impurities() (shared SemanticQueryEngine, cached)
4. Options: extra-impure (dotted -> module denylist, bare -> builtin denylist), allow fnmatch patterns matched against the call name and calling module path; ship default allowlist (logging.getLogger, warnings.filterwarnings, warnings.simplefilter)
5. Tests in tests/test_rule_import_time_side_effects.py covering all shapes, scopes, options, registration, opt-in
6. Dogfood on a /tmp copy of pypeeker; record findings (expect check/builtin/__init__.py import_submodules call at module scope)
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Added src/pypeeker/check/builtin/import_time_side_effects.py: project-scoped rule registered via @register_rule("import-time-side-effects", scope="project"), auto-discovered by check/builtin
- Import-time scopes computed per FileIndex: module scope plus CLASS/COMPREHENSION scopes whose enclosing scopes all run at import (function/lambda bodies break the chain)
- Three call shapes: bare/builtin vs policy.impure_builtins; IMPORT-rooted qualified calls (imported_from + chain[1:] + leaf, replicated from analysis/calls.py module_calls since that needs a function-scoped AnalysisContext) vs policy.module_impure_names; refs resolving via context.resolver to project FUNCTION/METHOD symbols with non-empty impurities() (shared SemanticQueryEngine, per-symbol cache)
- Options: extra-impure (dotted -> module denylist, bare -> builtin denylist, also flows into shape-3 purity analysis); allow fnmatch patterns matched against the called name AND the calling module path; DEFAULT_ALLOW ships logging.getLogger, warnings.filterwarnings, warnings.simplefilter (documented; user allow extends it)
- 20 tests in tests/test_rule_import_time_side_effects.py, all green; full suite green for my files (one pre-existing failure in another agent's in-progress test_rule_visibility.py, unrelated)
- Made the opt-in pyproject test resolve pyproject.toml from __file__ because tests/test_cli.py os.chdir()s without restoring (cwd-order hazard)
- Dogfood on a copy of pypeeker src: default policy -> 0 findings (importlib.import_module/pkgutil.iter_modules are not in the default module-impure denylist, so import_submodules analyzes as pure); with extra-impure=["importlib.import_module"] -> exactly src/pypeeker/check/builtin/__init__.py:44 import-time call to pypeeker.check.builtin:import_submodules resolves to an impure project function, validating shape 3 on real code; canary module with module-scope open() flagged through the real CLI (shape 1)
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added the opt-in `import-time-side-effects` project-scoped check rule: "imports must be free". It flags module-scope (and class-body, which also executes at import) CALL references that have side effects, one violation per offending call with 1-indexed lines.

Changes:
- New `src/pypeeker/check/builtin/import_time_side_effects.py`, auto-discovered and self-registered via `register_rule(..., scope="project")`. Detects three shapes: (1) bare/builtin calls matching the impure-builtin purity policy (`open`, `print`); (2) module-qualified calls matching the module-impure policy (`subprocess.run`, `time.time`), assembling `imported_from + chain[1:] + leaf` so aliased imports are caught; (3) calls resolving through the shared CrossModuleResolver to project FUNCTION/METHOD symbols whose `impurities()` is non-empty (shared engine, cached per symbol).
- Import-time scopes are computed structurally: module scope plus CLASS/COMPREHENSION scopes whose ancestors all run at import; function/lambda bodies are excluded.
- Options: `extra-impure` (dotted names join the module denylist, bare names the builtin denylist; also applies to shape-3 purity analysis) and `allow` fnmatch patterns matched against the called name and the calling module path. A documented default allowlist (`logging.getLogger`, `warnings.filterwarnings`, `warnings.simplefilter`) keeps conventional module-level setup quiet; user `allow` extends it. Opt-in: not enabled in pypeeker's own pyproject.

Tests:
- New `tests/test_rule_import_time_side_effects.py` (20 tests, green): module-scope open()/subprocess.run/aliased time.time flagged; impure project function call flagged (same-file and cross-file); pure project call and function/method-scope impure calls not flagged; class-body call flagged; default allowlist; allow by name / module path / symbol id; extra-impure dotted+bare; registration and opt-in checks.
- Dogfood on a copy of pypeeker src: default policy yields 0 findings (importlib.import_module is not in the default denylist, so import_submodules analyzes as pure); with `extra-impure=["importlib.import_module"]` the rule flags exactly `check/builtin/__init__.py:44` (module-scope `import_submodules(_self)`) as an import-time call to an impure project function — the expected validation finding.

Risks/follow-ups: purity analysis is heuristic; class instantiations at module scope are not analyzed through `__init__` (a CLASS resolution is skipped) — possible follow-up.
<!-- SECTION:FINAL_SUMMARY:END -->
