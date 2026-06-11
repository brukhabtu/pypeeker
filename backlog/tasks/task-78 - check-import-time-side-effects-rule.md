---
id: TASK-78
title: 'check: import-time-side-effects rule'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-06-11 18:25'
updated_date: '2026-06-11 18:37'
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
- [ ] #1 Rule flags module-scope calls matching the impure builtin/module policy, and module-scope calls to project functions whose impurities() is non-empty
- [ ] #2 Options: allow patterns (e.g. logging.getLogger), extra-impure; opt-in
- [ ] #3 Tests cover direct impure call, impure project-function call, and allowed patterns; dogfood run recorded
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
