---
id: TASK-68
title: 'check: impure-function rule with configurable purity policy'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 15:47'
updated_date: '2026-06-11 16:30'
labels:
  - check
  - analysis
dependencies:
  - TASK-66
  - TASK-67
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Purity policy tables (IMPURE_BUILTINS, MODULE_IMPURE_NAMES, TYPE_IMPURE_METHODS, ~100 entries) are frozen in code with no config hook, although check already has rule_options. Add an opt-in check rule that flags impure functions matching configured criteria, with policy overridable from pyproject.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 An opt-in check rule flags functions with impurity observations (scope configurable, e.g. by decorator/name-pattern/package)
- [x] #2 Policy tables can be extended/overridden via [tool.pypeeker.<rule>] options
- [x] #3 Rule uses the project-scoped rule context (cross-file call graph) and is tested
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. analysis/purity.py: add frozen PurityPolicy dataclass bundling the six policy tables (defaults = current module constants), DEFAULT_POLICY, and PurityPolicy.extended(extra_impure_builtins, extra_module_impure, extra_io_methods, allow) for additive/subtractive config without restating tables. Thread keyword-only policy=DEFAULT_POLICY through impurities()/observations()/_iter_observations and the filter helpers; existing call sites unchanged.
2. check/rules.py: add project-scoped opt-in rule no-impure-functions. Options: include (required fnmatch patterns on symbol_id or module path; empty=no-op), exclude, extra-impure (dotted->module denylist, bare->builtin denylist), allow. Iterate FUNCTION/METHOD symbols across ctx.indexes, run impurities() with a shared SemanticQueryEngine and configured policy, emit one one-line Violation per impure function naming first few observations with 1-indexed lines. Register in PROJECT_REGISTRY.
3. pyproject.toml: extend check allow-list with analysis and query (check now imports analysis.purity directly and query.engine for the shared engine).
4. Tests: tests/test_purity_policy.py for extended() mechanics + impurities(policy=...); TestNoImpureFunctions in tests/test_check_rules.py covering flag/pure/exclude/extra-impure/allow/include-required/opt-in.
5. Run uv run pytest -q and uv run pypeeker check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- analysis/purity.py: added frozen PurityPolicy dataclass bundling the six policy tables (defaults reference the existing module constants, so DEFAULT_POLICY == today's behavior), plus PurityPolicy.extended(extra_impure_builtins, extra_module_impure, extra_io_methods, allow) for additive/subtractive derivation. Threaded keyword-only policy=DEFAULT_POLICY through impurities(), observations(), _iter_observations and _filtered_attribute_method_calls; replaced _ALL_TRACKED_METHOD_NAMES with policy.tracked_method_names. All existing call sites unchanged.
- check/rules.py: new project-scoped opt-in rule no-impure-functions. Options: include (fnmatch on symbol_id or module path, REQUIRED — empty include is a documented no-op), exclude (wins over include), extra-impure (dotted names -> module denylist, bare names -> builtin denylist), allow (removed from every denylist). Iterates FUNCTION/METHOD symbols across ctx.indexes, shares one SemanticQueryEngine, emits one one-line ruff-style Violation per impure function naming up to 3 observations with 1-indexed lines (+N more). Registered in PROJECT_REGISTRY.
- pyproject.toml: check allow-list extended with analysis and query (rules.py now imports analysis.purity/analysis.observations and query.engine directly); uv run pypeeker check stays green.
- Tests: tests/test_purity_policy.py (extended() mechanics, impurities(policy=...) incl. transitive application); TestNoImpureFunctions in tests/test_check_rules.py (flag/pure/no-include-noop/exclude/extra-impure bare+dotted/allow/transitive/message truncation/methods/opt-in).
- Note: the purity.py PurityPolicy changes were swept into the orchestrator's TASK-63 commit (2e13a97) alongside the engine-injection work; remaining working-tree changes are rules.py, pyproject.toml, and tests.
- Verification: uv run pytest -q -> 638 passed, 10 skipped; uv run pypeeker check -> exit 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added an opt-in project-scoped check rule `no-impure-functions` backed by a newly configurable purity policy.

Changes:
- `analysis/purity.py`: frozen `PurityPolicy` dataclass bundles the purity denylists (impure builtins, I/O methods, collection mutations, module names, per-type methods, immutable receiver types); `DEFAULT_POLICY` reproduces existing behavior exactly. `PurityPolicy.extended(extra_impure_builtins=..., extra_module_impure=..., extra_io_methods=..., allow=...)` derives variants that add names or remove (`allow`) names from every denylist without restating the tables. `impurities()` and `observations()` take keyword-only `policy=DEFAULT_POLICY` (applied to the target and the whole transitive call walk); all existing call sites are unchanged.
- `check/rules.py`: new built-in project rule `no-impure-functions` (registered in PROJECT_REGISTRY, opt-in). Options under `[tool.pypeeker.no-impure-functions]`: `include` (fnmatch patterns on symbol_id or module path; required — enabling without scoping is a deliberate no-op), `exclude` (wins over include), `extra-impure` (dotted -> module denylist, bare -> builtin denylist), `allow` (un-denylists names). Runs `impurities()` with a shared SemanticQueryEngine over every matching FUNCTION/METHOD across the project context and emits one one-line violation per impure function naming the first 3 observation kinds/names with 1-indexed lines (`+N more`).
- `pyproject.toml`: `check` import-boundaries allow-list gains `analysis` and `query` (check now sits above analysis); `uv run pypeeker check` stays green.

Tests:
- New `tests/test_purity_policy.py`: extended() add/allow mechanics, allow-wins-over-extra, immutability, and `impurities(policy=...)` behavior incl. transitive purification.
- `tests/test_check_rules.py` TestNoImpureFunctions: flagging, pure functions, include-required no-op, exclude precedence, extra-impure (bare + dotted), allow, transitive flagging, message truncation, method scope, opt-in default.
- Full suite: 638 passed, 10 skipped.

Risks/notes: purity analysis is heuristic, hence the rule is opt-in and requires explicit `include` scoping; rule cost is O(functions in scope) impurity walks, acceptable for scoped use.
<!-- SECTION:FINAL_SUMMARY:END -->
