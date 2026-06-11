---
id: TASK-76
title: 'check: no-hidden-global-mutation rule (advisory)'
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
pylint flags the 'global' keyword; the real hazards are silent: mutating module-level containers from functions (receiver root resolves to a module-scope VARIABLE), attribute writes on imported modules, os.environ mutation. All visible to the existing receiver/write facts; no new analysis required.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Rule flags outer-scope writes targeting module scope, mutator calls on module-level variables, and attribute writes on IMPORT receivers, from inside functions
- [x] #2 Options: allow patterns; opt-in
- [x] #3 Tests cover each shape plus non-flagged local mutations; dogfood run recorded
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Probe binder facts for the three shapes (done: outer-scope writes incl. subscript, mutator CALL refs with module-level VARIABLE receiver root, attribute WRITE refs with IMPORT receiver)
2. New src/pypeeker/check/builtin/no_hidden_global_mutation.py: project-scoped rule via @register_rule; iterate FUNCTION/METHOD symbols, AnalysisContext.for_function with shared engine; shape 1+subscript via outer_scope_writes filtered to module-scope targets; shape 2 via CALL refs with receiver root = module-level VARIABLE and method in DEFAULT_POLICY.collection_mutation_names (+extra-mutators); shape 3 via attribute_writes filtered to ReceiverKind.IMPORT
3. Options: allow (fnmatch on function symbol_id/module path), extra-mutators; opt-in by virtue of registration-only
4. tests/test_rule_no_hidden_global_mutation.py covering all shapes + negatives + allow
5. Dogfood on /tmp copy of the repo; record findings in notes
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Probed binder facts: module scope id is the bare module path (no colon); `global x; x = 1` is modeled as a shadow VARIABLE definition (x$2) at module scope located inside the function body (no WRITE ref), while `x += 1` and subscript writes emit WRITE refs caught by outer_scope_writes. Rule covers both representations (shapes 1a/1b).
- Shape 2 via CALL refs whose receiver root is a module-scope VARIABLE and method is in DEFAULT_POLICY.collection_mutation_names (+ extra-mutators option). Shape 3 via attribute_writes filtered to ReceiverKind.IMPORT, naming the imported module via imported_from.
- Known gap (binder, out of scope): `os.environ["X"] = v` emits only READ refs (subscript store on an attribute chain produces no WRITE fact), so that idiom is not caught; documented in the rule docstring.
- Dogfood on /tmp copy of the repo with only this rule enabled: 2 findings, both genuine — pypeeker.check.rules:register_rule._decorate subscript-writes module-level _REGISTERED and _REGISTERED_PROJECT (the deliberate plugin-registry pattern; exactly what an allow pattern is for). No false-positive noise elsewhere.
- Tests: 19 in tests/test_rule_no_hidden_global_mutation.py; full suite 771 passed, 0 failures.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added the opt-in advisory rule no-hidden-global-mutation: flags mutations of module-level/global state from inside functions that pylint's syntactic global-keyword warning misses, using only existing binder facts (no new analysis).

Changes:
- NEW src/pypeeker/check/builtin/no_hidden_global_mutation.py — project-scoped rule, auto-discovered and self-registered via @register_rule. Iterates FUNCTION/METHOD symbols with AnalysisContext.for_function (shared SemanticQueryEngine). Detects four concrete shapes: (1a) outer-scope WRITE refs landing at module scope (augmented assignment, subscript writes on module-level containers) via analysis.writes.outer_scope_writes; (1b) plain `global x; x = 1` rebinds, which the binder models as shadow VARIABLE definitions at module scope inside the function body — attributed to the innermost containing function scope; (2) mutator method calls (DEFAULT_POLICY.collection_mutation_names, shared with purity — not copied) on receivers rooted at module-scope VARIABLEs; (3) attribute writes with IMPORT receivers (config.value = x), naming the imported module. Messages name the mutated global, the shape, and the function; lines are 1-indexed; output sorted/deduplicated.
- Options: allow (fnmatch on function symbol_id or module path), extra-mutators (extends the mutation table). Opt-in: not added to pypeeker's own pyproject rules.
- NEW tests/test_rule_no_hidden_global_mutation.py — 19 tests: all four flagged shapes incl. method bodies and nested-function attribution; negatives (local mutation, top-level initialization, nonlocal, pure reads, non-mutator methods); allow/extra-mutators options; registration + sort/dedupe.

Dogfood (on a /tmp copy): 2 findings, both real — register_rule._decorate subscript-writes the module-level _REGISTERED/_REGISTERED_PROJECT registries (deliberate pattern, suppressible via allow). No other noise.

Known gap: os.environ["X"] = v emits only READ refs from the binder (no WRITE fact for subscript stores on attribute chains), so it is not catchable today; documented in the rule docstring.

Tests: uv run pytest -q — 771 passed, 0 failures.
<!-- SECTION:FINAL_SUMMARY:END -->
