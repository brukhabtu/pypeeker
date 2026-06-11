---
id: TASK-76
title: 'check: no-hidden-global-mutation rule (advisory)'
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
pylint flags the 'global' keyword; the real hazards are silent: mutating module-level containers from functions (receiver root resolves to a module-scope VARIABLE), attribute writes on imported modules, os.environ mutation. All visible to the existing receiver/write facts; no new analysis required.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Rule flags outer-scope writes targeting module scope, mutator calls on module-level variables, and attribute writes on IMPORT receivers, from inside functions
- [ ] #2 Options: allow patterns; opt-in
- [ ] #3 Tests cover each shape plus non-flagged local mutations; dogfood run recorded
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Probe binder facts for the three shapes (done: outer-scope writes incl. subscript, mutator CALL refs with module-level VARIABLE receiver root, attribute WRITE refs with IMPORT receiver)
2. New src/pypeeker/check/builtin/no_hidden_global_mutation.py: project-scoped rule via @register_rule; iterate FUNCTION/METHOD symbols, AnalysisContext.for_function with shared engine; shape 1+subscript via outer_scope_writes filtered to module-scope targets; shape 2 via CALL refs with receiver root = module-level VARIABLE and method in DEFAULT_POLICY.collection_mutation_names (+extra-mutators); shape 3 via attribute_writes filtered to ReceiverKind.IMPORT
3. Options: allow (fnmatch on function symbol_id/module path), extra-mutators; opt-in by virtue of registration-only
4. tests/test_rule_no_hidden_global_mutation.py covering all shapes + negatives + allow
5. Dogfood on /tmp copy of the repo; record findings in notes
<!-- SECTION:PLAN:END -->
