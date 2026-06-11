---
id: TASK-80
title: 'check: unused-return-value rule (+ binder call-result-used fact)'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 18:26'
updated_date: '2026-06-11 18:58'
labels:
  - check
  - binder
  - m1-advisory
dependencies:
  - TASK-74
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
A function with a declared non-None return type whose result is discarded at every call site is a procedure pretending to be a function, or every caller is buggy. Needs one new binder fact: whether a CALL reference is a bare expression statement. Scope conservatively to declared non-None returns.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Binder records result_used (or equivalent) on CALL references — false when the call is a bare expression statement
- [x] #2 Project rule flags FUNCTION/METHOD symbols with a declared non-None return annotation whose every project call site discards the result
- [x] #3 Index serialization forward-compatible; tests cover used/discarded/mixed and None-returning exclusion; opt-in
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add result_used: bool = True to Reference (models/references.py) with docstring; default keeps old indexes deserializing via from_dict defaults
2. Add _call_result_discarded(call_node) helper in binder/references.py: parent is expression_statement, or parent is await whose parent is expression_statement; wire into visit_call (bare identifier) and visit_attribute_call (method calls, call node = attr_node.parent)
3. New builtin project rule src/pypeeker/check/builtin/unused_return_value.py: @register_rule("unused-return-value", scope="project"); candidates = FUNCTION/METHOD with declared non-None return annotation, skip dunders; one pass resolving project CALL refs via ctx.resolver; flag when >=1 call and all result_used=False; allow option (fnmatch); opt-in, pyproject untouched
4. tests/test_rule_unused_return_value.py: binder fact tests (bare/method/await discarded; assignment/return/argument/comparison used), serialization round-trip, rule tests (flagged/mixed/None-returning/unannotated/zero-call/allow/registration/opt-in)
5. uv run pytest -q; dogfood on /tmp copy of repo
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- result_used field on Reference was already in place from session start; wired the fact into visit_call (bare identifier calls) and visit_attribute_call (self/cls-resolved and unresolved method calls) via a _call_result_discarded helper (expression_statement parent, with one await hop)
- New builtin project rule check/builtin/unused_return_value.py: declared non-None return annotation, >=1 resolved call, all calls discarded; skips dunders, zero-call functions, and functions escaping as values (READ/DECORATOR refs); allow option (fnmatch on symbol_id or module path); opt-in, pyproject untouched
- tests/test_rule_unused_return_value.py: 33 tests (binder fact, serialization round-trip + missing-key default, rule flag/skip/options/registration/opt-in) — all green in isolation

- Dogfood (/tmp copy, rule enabled there only; repo pyproject untouched): pypeeker index src && pypeeker check found 7 genuine convenience-return findings — ScopeStack.pop (->Scope, 6 discarded sites), ScopeStack.declare_in_scope (->str, 4 sites), check.builtin:import_submodules (->list[str]), indexer:ensure_fresh (->IndexResult, cli.py:42), and the three *.save (->Path) methods (IndexStore/OverlayIndexStore/TreeStore). All accurate: callers use these for side effects. Note: src-only index, so test-suite call sites are invisible there
- ruff check passes on all touched files; ruff format not enforced repo-wide (every existing builtin rule module would also reformat)
- Full suite: 835 passed
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added an unused-return-value project check rule backed by a new binder fact recording whether each call's result is used.

Changes:
- `Reference.result_used: bool = True` (src/pypeeker/models/references.py): False only when a CALL's value is syntactically discarded; default True keeps previously written indexes deserializing unchanged (from_dict falls back to defaults, unknown keys ignored).
- Binder (src/pypeeker/binder/references.py): new `_call_result_discarded()` — the OUTERMOST call node (for `a.b()`, the attribute node's parent) whose parent is an `expression_statement`, with one `await` hop allowed. Wired into `visit_call` (bare identifier calls) and `visit_attribute_call` (self/cls-resolved method refs and unresolved attribute calls). Everything else — assignment, return, argument, comparison, chained receiver, yield, tuple — stays result_used=True.
- New builtin rule (src/pypeeker/check/builtin/unused_return_value.py, auto-discovered, opt-in): flags FUNCTION/METHOD symbols with a DECLARED non-None return annotation when they have >=1 resolved project call site and every one discards the result. Anchored at the definition (1-indexed); message lists up to 3 call sites. Skips dunders, `-> None` (incl. string spellings), zero-call functions (dead-code rules' turf), and functions that escape as values (READ/DECORATOR refs — alias calls are invisible). `allow` option fnmatches symbol_id or module path. pyproject untouched.

Tests (tests/test_rule_unused_return_value.py, 33 tests):
- binder fact: bare/method/unresolved/awaited calls discarded; assignment/return/argument/comparison/chained-receiver used; non-CALL refs unaffected
- serialization: JSON round-trip preserves the fact; old indexes without the key default to True
- rule: flagged (same-file, method, cross-file via import, async/await, >3-site message truncation), not flagged (mixed use, None/string-None, unannotated, zero calls, dunder, value escape), allow options, registration + opt-in (pyproject read anchored to __file__)

Verification: full suite 835 passed; ruff check clean. Dogfooded on a /tmp copy: 7 accurate advisory findings (ScopeStack.pop/declare_in_scope, import_submodules, ensure_fresh, the three store .save methods) — all side-effect-style callers of convenience returns.

Risks/follow-ups: rule sees only indexed src (test-suite call sites don't count); functions called solely through aliases are conservatively skipped.
<!-- SECTION:FINAL_SUMMARY:END -->
