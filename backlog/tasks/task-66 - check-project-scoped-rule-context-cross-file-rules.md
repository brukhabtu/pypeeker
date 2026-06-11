---
id: TASK-66
title: 'check: project-scoped rule context (cross-file rules)'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 15:47'
updated_date: '2026-06-11 16:19'
labels:
  - check
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Rule = (FileIndex, options) structurally prevents any rule that needs the resolver, the tree, or a second file — yet the most valuable upcoming semantic rules (unused public symbol, impure-function policy, dead re-exports) are cross-module, and the analysis layer they need already exists. Give rules a project-scoped context with the per-file signature kept as a convenience wrapper.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Rules can access all indexes, a shared CrossModuleResolver, and the symbol tree via a project-scoped context
- [x] #2 Existing per-file rules keep working unchanged (wrapper or adapter preserves the current Rule signature)
- [x] #3 At least one cross-file rule or test demonstrates the new capability
- [x] #4 register_rule plugin path supports project-scoped rules
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add src/pypeeker/check/context.py: CheckContext holding store + all FileIndexes under config.src, with lazily-built shared CrossModuleResolver and lazily-built symbol tree (build_tree).
2. pyproject.toml import-boundaries: extend check allow-list with "resolve" and "tree" (both depend only on layers below check).
3. rules.py: add ProjectRule = Callable[[CheckContext, Mapping], list[Violation]]; separate project registries (PROJECT_REGISTRY / _REGISTERED_PROJECT); register_rule(name, scope="file"|"project") keeps existing plugin signature working; get_project_rule lookup; implement unused-public-symbol as opt-in project-scoped built-in (module-level PUBLIC FUNCTION/CLASS with zero resolved references project-wide, skipping dunder/main/__main__.py and __init__ barrel re-exports; over-flagging caveats documented).
4. engine.py: partition enabled names into file vs project rules; run file rules per file as today; build CheckContext once (only when a project rule is enabled) and run project rules once.
5. __init__.py: export CheckContext and ProjectRule.
6. Tests: engine runs project plugin rule with context (indexes/resolver/tree/store); per-file behaviour unchanged; CheckContext not built for file-only runs; scope validation; unused-public-symbol flags unreferenced public fn/class, not referenced/barrel-exported/protected/non-module-level ones.
7. Run uv run pytest -q and self-lint.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Added src/pypeeker/check/context.py: CheckContext(store, indexes) with lazy cached resolver (CrossModuleResolver) and lazy tree (build_tree over the same indexes, not the persisted cache).
- rules.py: ProjectRule type alias; PROJECT_REGISTRY/_REGISTERED_PROJECT; register_rule(name, scope="file"|"project") — default keeps the existing plugin signature byte-compatible; get_project_rule lookup; ValueError on unknown scope.
- Implemented unused-public-symbol as an opt-in project-scoped built-in: one pass resolving every reference to canonical ids, skips non-public/non-module-level/dunder/main/__main__.py and __init__ barrel re-exports; over-flagging caveats (decorators, getattr, entry points) documented in the docstring.
- engine.py: partitions enabled names into file vs project rules (file wins on cross-scope name clash), collects indexes during the per-file pass, builds CheckContext only when a project rule is enabled, runs project rules once.
- pyproject.toml: check allow-list extended with "resolve" and "tree" (both lower layers). Included the tree because AC#1 names it; lazy so per-file runs never pay for it.
- Tests: 6 new engine tests (project plugin via module, context contents incl. resolver/tree/store, options, src filter, context-not-built-for-file-only-runs via monkeypatched bomb, end-to-end unused-public-symbol) + registry scope tests + 13 rule tests for unused-public-symbol. Full suite: 616 passed, 10 skipped. Self-lint (uv run pypeeker check) exits 0; opt-in run of unused-public-symbol on pypeeker itself flags nothing.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added a project-scoped rule context so check rules can finally see across files, with the existing per-file Rule signature untouched.

Changes:
- New src/pypeeker/check/context.py: CheckContext carries the IndexStore and every FileIndex under config.src, plus a lazily-built shared CrossModuleResolver and a lazily-built symbol tree (build_tree) — project rules pay only for what they touch.
- check/rules.py: new ProjectRule = (CheckContext, options) -> list[Violation] alongside the unchanged per-file Rule; separate built-in/custom registries per scope; register_rule(name, scope="project") for plugins (default scope="file" keeps existing plugins working unchanged; unknown scopes raise ValueError).
- check/engine.py: enabled rule names are partitioned by scope; per-file rules run per file exactly as before; the CheckContext is constructed only when at least one project rule is enabled, then project rules run once per check.
- New opt-in built-in cross-file rule unused-public-symbol: flags module-level public functions/classes with zero resolved references project-wide; conservatively skips dunder/main names, __main__.py, and symbols re-exported via __init__ barrels; dynamic-access over-flagging caveats documented.
- pyproject.toml: check import-boundaries allow-list extended with resolve and tree (both depend only on layers beneath check).
- check/__init__.py exports CheckContext and ProjectRule.

Tests:
- uv run pytest -q: 616 passed, 10 skipped (existing check tests untouched and green).
- New coverage: project plugin registration + execution with full context (indexes/resolver/tree/store), per-rule options, src filtering, proof the context is never built for per-file-only runs, and 13 behavioural tests for unused-public-symbol (referenced, same-file, aliased, barrel-exported, through-barrel, visibility, methods, dunder/main, 1-indexed lines, opt-in).
- Self-lint passes; running unused-public-symbol opt-in over pypeeker itself reports nothing.

Risks/follow-ups: unused-public-symbol is static-reference based and stays opt-in; TASK-68 (impure-function policy) can now build on CheckContext.
<!-- SECTION:FINAL_SUMMARY:END -->
