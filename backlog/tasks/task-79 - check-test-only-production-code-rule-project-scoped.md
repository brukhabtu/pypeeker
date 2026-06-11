---
id: TASK-79
title: 'check: test-only-production-code rule (project-scoped)'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 18:26'
updated_date: '2026-06-11 18:41'
labels:
  - check
  - m1-advisory
dependencies:
  - TASK-74
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
A production symbol whose only references come from test paths should not be public production API (and may be dead). Requires cross-module reference truth: resolver + path classification. Builds on the CheckContext from task-66.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Rule flags module-level production symbols all of whose project references originate from configured test globs (default tests/)
- [x] #2 Zero-reference symbols are excluded (that is unused-public-symbol's job); barrel re-exports excluded
- [x] #3 Options: test path globs, allow patterns; opt-in; tests cover test-only, prod-used, and mixed usage
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. New src/pypeeker/check/builtin/test_only_production_code.py: @register_rule("test-only-production-code", scope="project"); classify index file paths against test-globs (fnmatch, default ["tests/**", "test_*.py", "**/test_*.py"]); collect barrel-exported canonical ids (IMPORT symbols in __init__.py); for each public module-level FUNCTION/CLASS in non-test files (skipping dunder/main/__main__.py/allow patterns/barrel-exported), partition resolver.references_to_definition by test vs production origin (excluding the definition site); flag when production refs == 0 and test refs > 0.
2. New tests/test_rule_test_only_production_code.py using indexed_project fixture: test-only flagged; prod+test not; zero-ref not; barrel-exported not; test-file definitions skipped; custom test-globs; allow suppression; 1-indexed line; registry presence; opt-in (not in pyproject rules).
3. Dogfood on a /tmp copy (index src AND tests), record findings.
4. uv run pytest -q green.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Implemented as auto-discovered builtin (src/pypeeker/check/builtin/test_only_production_code.py), registered via @register_rule("test-only-production-code", scope="project"); imports only concrete modules (check.rules/check.models/check.context).
- Mechanics: test classification by fnmatch of index file_path against test-globs (default ["tests/**", "test_*.py", "**/test_*.py"]); barrel-exported canonicals (IMPORT symbols in __init__.py, resolver.resolve_definition) excluded entirely; per public module-level FUNCTION/CLASS in non-test files, resolver.references_to_definition partitioned into prod/test by origin path, definition site excluded; flag only when prod==0 and test>0 (zero-ref is unused-public-symbol's job). Reuses unused-public-symbol skip conventions (dunder, main, __main__.py) plus _as_str_list/_matches_any helpers.
- Dogfood on /tmp copy (indexed src AND tests, rules=["test-only-production-code"]): 8 hits. 4 are the new builtin rule functions (registry/decorator dispatch = known dynamic-use false-positive class, same caveat as unused-public-symbol). 4 look genuine: models.capabilities:Capability, models.symbol_id:unresolved_attr_id, strip_shadow, shadow_suffix — all referenced only from tests.
- Edge found while dogfooding: default glob **/test_*.py classifies the rule's own module (test_only_production_code.py) as a test file — pytest naming-convention collision; overridable via test-globs, documented behavior.
- Pytest collection gotcha: rule function name starts with test_, so the test module imports it under an alias.
- Tests: 13 in tests/test_rule_test_only_production_code.py; full suite 746 passed, 0 failed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added the opt-in project-scoped check rule test-only-production-code: a module-level public function/class defined in production code whose only project references originate from test paths is flagged ("'X' is referenced only from tests (N test references)", 1-indexed at the definition).

Changes:
- New src/pypeeker/check/builtin/test_only_production_code.py (auto-discovered builtin, register_rule scope="project"). References are gathered via CheckContext.resolver.references_to_definition, so import aliases, barrels, and attribute access all count; origins are classified against configurable test-globs (default ["tests/**", "test_*.py", "**/test_*.py"], fnmatch). Flags only when production refs == 0 AND test refs > 0 — zero-reference symbols stay unused-public-symbol's territory. Barrel re-exported symbols (IMPORT in __init__.py) are excluded entirely; dunder/main/__main__.py skipped per existing convention; "allow" fnmatch patterns suppress symbols. Opt-in: not added to pypeeker's own rules.
- New tests/test_rule_test_only_production_code.py: 13 tests covering test-only flagged, prod+test mixed not flagged, same-module use counts as production, zero-ref excluded, barrel excluded, test-file definitions out of scope, custom test-globs, allow patterns, private/nested skips, violation shape, registry, opt-in.

Tests: uv run pytest -q — 746 passed, 0 failed; rule file green in isolation.

Dogfood (on /tmp copy, src+tests indexed): 8 hits — 4 registry-dispatched builtin rule functions (known dynamic-use false-positive class, same as unused-public-symbol) and 4 plausible real findings (models.capabilities:Capability; models.symbol_id:unresolved_attr_id, strip_shadow, shadow_suffix).

Risks/notes: default glob **/test_*.py also classifies production modules named test_*.py as tests (this rule's own module included) — documented, overridable via test-globs.
<!-- SECTION:FINAL_SUMMARY:END -->
