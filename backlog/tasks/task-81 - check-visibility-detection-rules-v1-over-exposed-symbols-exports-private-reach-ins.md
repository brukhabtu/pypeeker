---
id: TASK-81
title: >-
  check: visibility-detection rules v1 (over-exposed symbols, exports, private
  reach-ins)
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 18:26'
updated_date: '2026-06-11 18:47'
labels:
  - check
  - visibility
  - m1-advisory
dependencies:
  - TASK-74
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Minimal-visibility principle, detection only: compute observed usage scope per symbol from resolved references and compare to declared visibility. Three rules: over-exposed-module-symbol (public, never referenced outside its module), over-exposed-export (barrel export no other package consumes), under-exposed-access (_private symbols referenced from outside their module, incl. tests).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 over-exposed-module-symbol flags public module-level symbols with zero cross-module references (dunder/main/dynamic-decorator allowlist exempt)
- [x] #2 over-exposed-export flags __init__ re-exports never consumed outside the package
- [x] #3 under-exposed-access flags cross-module references to single-underscore symbols, with test paths reported distinctly
- [x] #4 All three opt-in with allow options; tests per rule; dogfood run over pypeeker recorded in notes
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Shared core in NEW src/pypeeker/check/builtin/visibility.py: map file_path->module via each index MODULE symbol; one pass over all references resolving each via context.resolver.resolve_reference to build canonical-def -> set(origin modules); package = leading dotted segments.
2. Rule over-exposed-module-symbol: public module-level FUNCTION/CLASS (kinds option, VARIABLE optional) with no reference originating outside its own module; exempt dunders, main, __main__.py, allow-decorators matches, barrel-exported defs, allow fnmatch.
3. Rule over-exposed-export: IMPORT symbols in __init__.py whose canonical definition is a real in-package definition; flag when every reference to that definition originates inside the package; allow fnmatch.
4. Rule under-exposed-access: iterate references; resolve target; if target visibility PROTECTED/PRIVATE (skip DUNDER) and origin module != defining module, flag at ref site; classify origin via test-globs option (default tests/**, test_*.py, *_test.py) and word message "accessed from tests" vs prod; allow fnmatch.
5. All three @register_rule(..., scope="project"), opt-in (not added to pyproject rules).
6. NEW tests/test_rule_visibility.py covering happy + exempt paths per rule using indexed_project + CheckContext (model: TestUnusedPublicSymbol).
7. Dogfood: copy src+tests to /tmp, pypeeker index, run the three rules via CheckContext; record findings in notes.
8. uv run pytest -q green.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented all three rules in NEW src/pypeeker/check/builtin/visibility.py (auto-discovered, @register_rule scope="project", opt-in — not added to pyproject rules). Shared core: _usage_origins() does one pass over all references, resolving each via CrossModuleResolver.resolve_reference into canonical-definition -> set(origin modules); origin module comes from each index's MODULE symbol. Three thin rules over it. NEW tests/test_rule_visibility.py: 23 tests, all green; full suite 771 passed.

Dogfood (indexed src+tests copy at /tmp/pypeeker-dogfood, 119 files):

- over-exposed-module-symbol: 416 total, but 385 in tests/ — every pytest test function is a public module-level function with zero cross-module refs. First read on the thesis: the rule needs the engine's src scoping (normal `pypeeker check` only feeds src/) or a test-file exemption when tests are in the indexed tree. The 31 src/ findings are accurate: e.g. binder visitor helpers (declare_import, visit_with_item, receiver_metadata), ScopeEntry, LanguageAdapter (only mentioned in a docstring) — genuine _-candidates. 17 of 31 are click CLI commands in cli.py reached via @cli.command decorators, exactly the allow-decorators case (allow-decorators=["cli.command*","cli.group*"] drops src findings 31->15). Notable thesis caveat found: result-object classes (IndexResult, RebuildResult, RangeDataFlow) get flagged although instances cross module boundaries — consumers touch them only via attribute access on call results, never by name, so name-reference scope undersells real exposure of returned types.

- over-exposed-export: 5 findings, all verified true positives (grep confirms no outside consumer): pypeeker.binder exports visit_module, visit_node, BinderState; pypeeker.check exports Rule, ProjectRule. All five are deliberate-API-looking but statically unconsumed — exactly the rule's thesis; Rule/ProjectRule are type aliases plausibly kept for plugin authors (allow option is the escape hatch). Refinement made during dogfood: a first run flagged `import pypeeker.check.builtin as _self` in check/builtin/__init__ — underscore-named imports aren't exports, so the rule now only considers PUBLIC import names.

- under-exposed-access: 10 findings, 0 production reach-ins from real production code — strong support for the thesis that the codebase's underscore discipline holds. 7 are test-origin: tests reach into check.rules._REGISTERED/_REGISTERED_PROJECT (registry cleanup in test teardown) — reported with the distinct "accessed from tests" wording. The other 3 are real cross-module reach-ins from the (parallel-task) builtin module test_only_production_code.py into check.rules._as_str_list/_matches_any, but they were classified as test-origin because the default test-glob "*/test_*.py" matches the rule module's *filename* (test_only_...). Glob-precision caveat recorded: pytest-convention filename globs can misclassify production files whose names start with test_; per-project test-globs option is the remedy.

Dogfood method: index_path over /tmp/pypeeker-dogfood/{src,tests}, CheckContext over all 119 indexes, rules invoked directly. No index/check run against the repo itself.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added three opt-in visibility-detection rules (minimal-visibility principle, detection only) as auto-discovered builtin project-scoped rules in NEW src/pypeeker/check/builtin/visibility.py, sharing one usage-scope core.

Changes:
- Shared core: one pass over all project references, each resolved through CrossModuleResolver.resolve_reference (import aliases, barrels, qualified attribute access) into a canonical-definition -> origin-modules map; origin module derived from each index's MODULE symbol.
- over-exposed-module-symbol: public module-level symbols (kinds option: function+class default, variable opt-in) with zero references outside their defining module — "make it _X". Exempt: dunders, main, __main__.py, barrel-exported definitions, decorators matching allow-decorators (registries/entry points), allow fnmatch.
- over-exposed-export: PUBLIC imports in a package __init__.py resolving to an in-package definition where every reference to that definition originates inside the package — "drop the re-export". External/cross-package/underscore imports skipped; allow matches export id or canonical id.
- under-exposed-access: each resolved reference to a PROTECTED/PRIVATE target from outside its defining module, one violation per reference site, dunders skipped; test-file origins (test-globs option, default tests/ dirs + test_*.py/*_test.py/conftest.py) reported with distinct "accessed from tests" wording; allow matches the target.
- All three registered via @register_rule(scope="project"), opt-in (not in pyproject rules); 1-indexed lines; concrete-module imports only (no pypeeker.check cycle).

Tests: NEW tests/test_rule_visibility.py — 23 tests covering happy + exempt paths per rule (same-module-only flagged, cross-module use not, barrel exemption, unconsumed vs consumed export, intra-package-only consumption, external imports, prod vs test reach-in wording, same-module/dunder skips, kinds/allow/allow-decorators/test-globs options, registration, opt-in). Green in isolation; full suite 771 passed.

Dogfood (src+tests indexed in /tmp copy): rule 1 — 31 accurate src findings (binder visitor helpers, click commands; allow-decorators cuts those) but 385 test functions flagged when tests are in the tree, and returned result-object classes (IndexResult etc.) over-flagged since instances escape by value, not by name. Rule 2 — 5 verified-true unconsumed exports (binder visit_module/visit_node/BinderState, check Rule/ProjectRule). Rule 3 — zero production reach-ins; 7 test reach-ins into rules._REGISTERED*; default test_*.py glob misclassified one production module named test_only_*.py (test-globs option is the remedy).

Risks/follow-ups: static-reference signal over-flags dynamically reached symbols (documented; allow/allow-decorators escape hatches); consider a test-file definition exemption for rule 1 when tests are indexed.
<!-- SECTION:FINAL_SUMMARY:END -->
