---
id: TASK-75
title: 'check: no-argument-mutation rule (advisory)'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 18:25'
updated_date: '2026-06-11 18:41'
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
Functions that mutate their parameters have caller-visible side effects no linter can see. Pypeeker already classifies receiver kinds: parameter-receiver collection mutations, attribute writes, and subscript writes on parameters are detectable today. Flag them; self/cls excluded; configurable allowlist (e.g. methods documented as mutating).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Rule flags collection-mutator calls, attribute writes, and subscript writes whose receiver root is a PARAMETER (self/cls excluded)
- [x] #2 Options: allow (function-id patterns), extra mutator names; opt-in rule
- [x] #3 Tests cover each mutation shape plus non-flagged local/self cases; dogfood run on pypeeker recorded in notes
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. New builtin rule module src/pypeeker/check/builtin/no_argument_mutation.py registered via @register_rule("no-argument-mutation", scope="project")
2. Iterate FUNCTION/METHOD symbols across context.indexes; build AnalysisContext.for_function per function (shared SemanticQueryEngine)
3. Detect three mutation shapes from ctx.file_index.references scoped to ctx.subtree: (a) CALL attribute refs whose leaf is in DEFAULT_POLICY.collection_mutation_names (+extra-mutators) and classify_receiver == PARAMETER; (b) WRITE attribute refs with PARAMETER receiver; (c) bare WRITE refs whose symbol is a PARAMETER (subscript write), excluding self/cls
4. Options: allow (fnmatch on function symbol_id, skips function), extra-mutators (extends mutator names)
5. Messages: function id + parameter name + mutation kind; 1-indexed lines
6. Tests in tests/test_rule_no_argument_mutation.py using indexed_project + CheckContext (per test_check_rules.py conventions); opt-in test against pyproject rules list
7. Dogfood: copy repo to /tmp (minus .semantic-tool), index there, run rule, record findings in notes
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Implemented src/pypeeker/check/builtin/no_argument_mutation.py: project-scoped rule registered via @register_rule("no-argument-mutation", scope="project"), auto-discovered by check/builtin. Detects three shapes per FUNCTION/METHOD (AnalysisContext.for_function with a shared SemanticQueryEngine): collection-mutator calls on PARAMETER receivers (mutator table from DEFAULT_POLICY.collection_mutation_names + extra-mutators option), attribute writes on PARAMETER receivers, and bare WRITE refs whose symbol is a PARAMETER (subscript writes). self/cls excluded via ReceiverKind.SELF / name check; locals never flagged. allow option = fnmatch on function symbol_id.
- Known coarseness documented in module docstring: augmented assignment to a parameter (x += 1) shares the subscript-write reference shape so it is flagged too; aliasing is invisible.
- Tests: tests/test_rule_no_argument_mutation.py (17 tests) covering each mutation shape, nested receiver chains, method non-self params, local/self/cls non-flagging, allow patterns, extra-mutators, registration, and opt-in (not in pyproject rules, not in builtin dict registries).

- Dogfood (repo copied to /tmp/pypeeker-dogfood-75 minus .semantic-tool, indexed there, rules=["no-argument-mutation"]): 60 findings.
  - 50/60 in pypeeker/binder/* — the deliberate BinderState-threading pattern (state.symbols.append, state.declaration_nodes.add, state.scope_stack.pop, ...). Intentional architecture; exactly what the allow option is for.
  - scope_stack.py: ScopeStack.declare/declare_in_scope mutate parameter symbol via attribute write .symbol_id — genuine caller-visible mutation worth knowing about.
  - indexer.py: _index_file mutates result accumulator (indexed.append/errors.append, intentional); ensure_fresh flagged store.remove() — receiver-type-blind over-match ("remove" is a collection mutator name but IndexStore.remove is file deletion); advisory-acceptable.
  - treebuild.py _ensure_ancestors and check/builtin/import_time_side_effects.py _describe_call: subscript writes into dict params used as caches/accumulators (intentional).
  - Verified allow option through real config: adding [tool.pypeeker.no-argument-mutation] allow=["pypeeker.binder.*"] drops findings 60 -> 5.
- Test results: tests/test_rule_no_argument_mutation.py 17/17 pass in isolation; full suite 744 passed with 2 failures in OTHER agents' files (test_rule_import_time_side_effects.py, test_rule_no_hidden_global_mutation.py — one of them leaks a cwd change; hardened my opt-in test to read pyproject.toml via __file__ so it is order-independent).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added the opt-in advisory check rule no-argument-mutation: flags functions that mutate their parameters (caller-visible side effects).

Changes:
- New src/pypeeker/check/builtin/no_argument_mutation.py — project-scoped rule, self-registered via register_rule and auto-discovered by check/builtin. Per FUNCTION/METHOD (AnalysisContext.for_function, shared query engine) it flags three mutation shapes: collection-mutator calls whose receiver root is a PARAMETER (mutator names from analysis.purity DEFAULT_POLICY, no table duplication), attribute writes on parameter receivers, and subscript writes whose root symbol is a parameter. self/cls and local-variable mutations are never flagged. Violations carry the function symbol_id, the parameter name, and how it was mutated, at the 1-indexed mutation line.
- Options: allow (fnmatch patterns on function symbol_id — skip those functions) and extra-mutators (names added to the mutator set).
- Opt-in: not added to any default rules list; enable via [tool.pypeeker].rules.
- New tests/test_rule_no_argument_mutation.py (17 tests): each flagged shape incl. nested receiver chains and non-self method params; local/self/cls negative cases; allow and extra-mutators options; registration and opt-in status.

Tests: uv run pytest tests/test_rule_no_argument_mutation.py -q -> 17 passed; full suite green for this file.

Risks/notes: advisory by design — augmented assignment to a parameter shares the binder's subscript-write reference shape and is flagged too; mutator-name matching is receiver-type-blind (e.g. IndexStore.remove matches "remove"). Dogfood on a /tmp copy of pypeeker: 60 findings (50 are the binder's intentional BinderState threading; allow=["pypeeker.binder.*"] reduces to 5), details in notes.
<!-- SECTION:FINAL_SUMMARY:END -->
