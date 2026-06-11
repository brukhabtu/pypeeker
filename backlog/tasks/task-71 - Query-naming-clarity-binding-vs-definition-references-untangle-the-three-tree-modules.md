---
id: TASK-71
title: >-
  Query naming clarity: binding vs definition references; untangle the three
  tree modules
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 15:47'
updated_date: '2026-06-11 16:52'
labels:
  - query
  - clarity
dependencies:
  - TASK-62
priority: low
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
find_references (exact binding-id match) vs find_all_references (resolved-definition match) differ in a way the names do not express, and the CLI --all flag has the same problem. Separately, pypeeker/tree.py, models/tree.py, and storage/tree_store.py are a three-way 'tree' collision; the top-level one is really tree reconciliation/build logic.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Query method names (or at minimum docstrings + CLI help) express the binding-vs-definition distinction; CLI refs help text disambiguates --all
- [x] #2 pypeeker/tree.py is renamed or repackaged so the three tree modules are distinguishable; imports and layering config updated
- [x] #3 Full test suite passes
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Rename query methods: engine.find_references -> references_to_binding; engine/resolver find_all_references -> references_to_definition; *_classified -> references_to_definition_classified. Update docstrings to spell out binding vs definition semantics.
2. Update all call sites: cli.py refs command, refactor/planner.py, refactor/inline.py, tests/test_query_engine.py, tests/test_resolve.py; refresh CLI refs docstring + --all help text (keep TASK-72 resolution-kind text).
3. git mv src/pypeeker/tree.py -> src/pypeeker/treebuild.py; update importers (query/engine.py, cli.py, check/context.py, tests/test_tree.py) and module docstring cross-refs.
4. pyproject.toml import-boundaries: rename tree key -> treebuild and update allow-lists for check and query.
5. Rename tests/test_tree.py -> tests/test_treebuild.py for symmetry.
6. Grep architecture.md for stale names and patch surgically.
7. Verify: uv run pytest -q (zero failures) and uv run pypeeker check self-check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Renamed engine queries: find_references -> references_to_binding (exact binding-id match, never crosses modules), find_all_references -> references_to_definition, find_all_references_classified -> references_to_definition_classified; same rename on CrossModuleResolver. No back-compat aliases.
- Updated all call sites: cli.py refs command, refactor/planner.py, refactor/inline.py, tests/test_query_engine.py, tests/test_resolve.py (test names renamed to match).
- Rewrote CLI refs docstring + --all help: default = same-binding usages only (does not cross modules); --all = usages of the resolved definition via imports/re-exports/receivers. Kept the TASK-72 resolution-kind documentation verbatim.
- git mv src/pypeeker/tree.py -> src/pypeeker/treebuild.py; module docstring now states it owns build/reconcile logic only (data model in models.tree, persistence in storage.tree_store). Updated importers: cli.py, query/engine.py (get_tree lazy import), check/context.py docstring ref, tests.
- Renamed tests/test_tree.py -> tests/test_treebuild.py via git mv.
- pyproject.toml import-boundaries: tree key -> treebuild; updated check and query allow-lists. architecture.md layering section updated to match.
- Verified boundary enforcement with a negative probe (treebuild = [] produced import-boundaries violations naming treebuild, then restored). uv run pypeeker check after re-index shows only the two pre-existing violations (rules.py:450 no-unresolved-refs, applier.py:179 require-docstrings) — confirmed identical at baseline via git stash.
- uv run pytest -q: 661 passed, 0 failures (the 10 baseline skips now run because the self-index exists).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Renamed the reference queries so binding-vs-definition semantics are in the name, and untangled the three-way tree module collision.

Changes:
- SemanticQueryEngine: find_references -> references_to_binding (exact binding-id match; a consumer module's usages bind to its local IMPORT symbol, so this never crosses modules), find_all_references -> references_to_definition (resolves every reference to its canonical definition; crosses imports/barrels/receivers), find_all_references_classified -> references_to_definition_classified. CrossModuleResolver mirrors the latter two renames. No back-compat aliases (pre-1.0 internal API); all call sites updated (cli.py, refactor/planner.py, refactor/inline.py, tests).
- Method docstrings now spell out the binding vs definition distinction explicitly and cross-reference each other.
- CLI refs command keeps its name; its docstring and --all help now state: default = same-binding usages only (does not cross modules); --all = usages of the resolved definition reached through import aliases, __init__.py re-exports, and receiver attribute access. The TASK-72 resolution-kind ("resolution" field) documentation is preserved.
- git mv src/pypeeker/tree.py -> src/pypeeker/treebuild.py so the three tree modules are distinguishable: treebuild.py (build/reconcile logic), models/tree.py (TreeIndex/TreeNode data), storage/tree_store.py (persistence) — the docstring now says so. Importers updated (cli.py, query/engine.py lazy import, check/context.py docstring). tests/test_tree.py -> tests/test_treebuild.py.
- pyproject.toml [tool.pypeeker.import-boundaries.allow]: tree key renamed to treebuild and updated in the check/query allow-lists; architecture.md layering section updated to match.

Tests:
- uv run pytest -q: 661 passed, 0 failures (baseline 651 passed + 10 skipped; the skips now execute because the self-index was rebuilt).
- uv run pypeeker index src && uv run pypeeker check: only the two pre-existing violations remain (verified identical at baseline via git stash); a negative probe with treebuild = [] confirmed the renamed boundary key is enforced.

Risks: none beyond the rename surface; no behavior changes — names, docstrings, imports, and config keys only.
<!-- SECTION:FINAL_SUMMARY:END -->
