---
id: TASK-77
title: 'check: pure-decorator-contracts rule (impure @cache/@property/dunders)'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 18:25'
updated_date: '2026-06-11 18:40'
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
Memoizing or caching an impure function is a bug (@lru_cache on time/I-O); @property doing I/O and impure __eq__/__hash__/__repr__/__len__ violate implicit contracts. Symbols carry decorators and impurities() exists — compose them. Configurable decorator list and dunder list.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Rule flags functions with configured decorators (default cache/lru_cache/cached_property/property) whose impurities() is non-empty
- [x] #2 Rule flags configured dunders (default eq/hash/repr/len/str) that are impure
- [x] #3 Violation message names the impurity observations; tests cover decorated/dunder pure+impure; opt-in
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Read patterns from check/rules.py (no-impure-functions) and check/builtin discovery
2. Implement src/pypeeker/check/builtin/pure_decorator_contracts.py: project-scoped rule with decorator-contract and dunder-contract checks over FUNCTION/METHOD symbols, shared SemanticQueryEngine, options decorators/dunders/allow
3. Write tests/test_rule_pure_decorator_contracts.py covering decorated impure/pure, impure property, impure/pure dunders, undecorated impure non-dunder not flagged, allow suppression, option overrides, opt-in
4. Dogfood on /tmp copy of repo; run full pytest
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Implemented src/pypeeker/check/builtin/pure_decorator_contracts.py: project-scoped rule, self-registers via register_rule
- Decorator contract matches leading dotted name or its last segment (handles functools. prefixes + call parens); dunder contract restricted to METHOD symbols
- Shared SemanticQueryEngine passed to impurities(); decorator contract wins when both apply
- 16 tests green in isolation (after fixing store-persistence across _run calls in one test)

- Dogfood on /tmp copy (rules=["pure-decorator-contracts"], pypeeker index src + check): exit 0, zero findings on real source — notably the lazy-memoizing CheckContext.resolver/.tree @property pattern (self._x = ... assignment) is NOT flagged, which is the desirable behavior
- Planted an impure @lru_cache function + impure __repr__ in the copy: both flagged through the full CLI path with correct ruff-style output and 1-indexed lines (def line + observation line), exit 1
- Full suite: my 16 tests green in isolation and in full run; fixed a cwd-dependence in test_not_in_default_rules by anchoring pyproject path to __file__ (a CLI test earlier in the run changes cwd). One unrelated failure remains in tests/test_rule_test_only_production_code.py (parallel agent's file, same cwd issue pattern)
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added the opt-in pure-decorator-contracts project rule: caching/memoizing decorators and contract dunders imply purity, so an impure body under them is a bug.

Changes:
- New src/pypeeker/check/builtin/pure_decorator_contracts.py, auto-discovered and self-registered via register_rule("pure-decorator-contracts", scope="project"). Two checks over FUNCTION/METHOD symbols: (1) decorator contract — configured pure-contract decorators (default cache/lru_cache/cached_property/property), matching the decorator's leading dotted name or its last segment so functools./builtins prefixes and call parens like lru_cache(maxsize=None) are handled; (2) dunder contract — configured dunders (default __eq__/__hash__/__repr__/__str__/__len__), METHOD symbols only. Both call analysis.purity.impurities() with one shared SemanticQueryEngine; decorator contract wins when both apply.
- Options: decorators (override list), dunders (override list), allow (fnmatch on symbol_id). Violation message names the contract plus the first 3 observation kinds/names with 1-indexed lines.
- New tests/test_rule_pure_decorator_contracts.py (16 tests): impure @lru_cache/@property/@cached_property/@functools.cache flagged; pure @cache and pure __eq__ not flagged; impure __repr__ flagged; non-contract dunder (__enter__) and undecorated impure non-dunder not flagged (no-impure-functions territory); allow patterns; decorators/dunders overrides; transitive impurity through @cache; registration + opt-in assertions.

Tests:
- uv run pytest tests/test_rule_pure_decorator_contracts.py -q (16 passed)
- Full suite green except one unrelated failure in a parallel agent's test file.

Dogfood (on a /tmp copy with the rule enabled): zero findings on pypeeker's own source — lazy self-attribute memoization in @property is not flagged (good); planted impure @lru_cache and impure __repr__ were both caught end-to-end via the CLI.
<!-- SECTION:FINAL_SUMMARY:END -->
