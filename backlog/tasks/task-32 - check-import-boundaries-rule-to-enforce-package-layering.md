---
id: TASK-32
title: 'check: import-boundaries rule to enforce package layering'
status: Done
assignee:
  - '@claude'
created_date: '2026-05-23 23:22'
updated_date: '2026-05-23 23:26'
labels:
  - check
  - architecture
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Adds a configurable layering rule to pypeeker check so architectural boundaries are enforced (and regressions prevented) by the tool itself. Motivated by an audit that found tree.py reaching into the binder package for a path helper — a layering leak nothing detects today (check only has require-docstrings and no-unresolved-refs).

The rule reads the MODULE symbol of each file (its dotted module path) and its IMPORT symbols (imported_from), maps both to their top-level package under the project root, and flags an internal import whose target package is not in the importing package allow-list. External imports (different root) and same-package imports are ignored; packages not listed in the config are unconstrained (incremental adoption).

To dogfood it green, module_path_from is relocated from binder.helpers to a new pure leaf module pypeeker/paths.py (it is a generic file-path -> dotted-module-path helper, not a binding concern), removing the tree -> binder and indexer -> binder edges. pypeeker enables the rule on itself with its real layering.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A new import-boundaries rule maps each file's module and its imports to top-level packages and flags imports not permitted by an allow-list; same-package and external (different-root) imports are never flagged
- [x] #2 Config shape: [tool.pypeeker.import-boundaries] with an allow table (package -> list of allowed package deps) and optional root; packages absent from allow are unconstrained; the rule is opt-in via the rules list
- [x] #3 module_path_from is moved to a new dependency-free pypeeker/paths.py and all call sites (binder, indexer, tree, refactor, tests) updated; binder no longer needs it from a non-leaf location and tree no longer imports binder
- [x] #4 pypeeker enables import-boundaries on itself with its actual layering and pypeeker check exits 0 (the rule would have flagged the former tree->binder leak)
- [x] #5 Unit tests cover: a forbidden cross-package import is flagged, an allowed one is not, same-package and external imports are ignored, and unlisted packages are unconstrained; full suite green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Create pypeeker/paths.py with module_path_from (pure, no internal deps); remove it from binder/helpers.py.
2. Update imports: binder/binder.py, indexer.py, tree.py, refactor/applier.py, tests/test_tree.py, tests/test_resolve.py -> from pypeeker.paths import module_path_from.
3. Add import-boundaries rule in check/rules.py: read MODULE symbol module path + IMPORT imported_from; _package_under(path, root) -> first segment under root (None if outside root); flag dep_pkg not in allow[imp_pkg]; skip same-pkg, external, unlisted importer pkgs. Register in REGISTRY with constant IMPORT_BOUNDARIES.
4. Wire pypeeker pyproject: add rule to rules list + [tool.pypeeker.import-boundaries.allow] with the real layering (root pypeeker; cli unlisted=unconstrained).
5. Tests in test_check_rules.py: forbidden flagged, allowed ok, same-pkg ignored, external ignored, unlisted importer unconstrained, line 1-indexed.
6. uv run pypeeker index src && pypeeker check (exit 0); full suite.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented:
- pypeeker/paths.py: new dependency-free leaf module; module_path_from moved here from binder.helpers. Updated call sites in binder/binder.py, indexer.py, tree.py, refactor/applier.py and tests (test_tree.py, test_resolve.py). tree no longer imports binder; binder pulls the path helper from a leaf.
- check/rules.py: import_boundaries rule + IMPORT_BOUNDARIES constant, registered. Reads the file MODULE symbol (module path) and IMPORT.imported_from; _package_under(path, root) maps to the first package segment under the project root (None if outside root or the root itself). Flags dep_pkg not in allow[importer_pkg]; skips same-package, external (different root), and unlisted importer packages. root defaults to the importing module first segment.
- pyproject.toml: enabled import-boundaries with root=pypeeker and the real allow table (cli left unlisted/unconstrained).
- architecture.md: documented the enforced layering and how the rule works.

Verified: pypeeker check exits 0 with the rule on. Live-proof: temporarily removing storage from query allow made check flag "package query may not import storage" on engine.py:12 (two imports), exit 1 — confirming the rule is wired and reads config. 403 tests pass incl. 8 new import-boundaries unit tests (forbidden flagged, allowed ok, same-package/external/unlisted ignored, root inference, no-op without config, 1-indexed line).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added an import-boundaries rule to pypeeker check so package layering is enforced (and regressions caught) by the tool itself, and fixed the one boundary leak it surfaced.

What changed:
- check gains an import-boundaries rule: it maps each file (via its MODULE symbol) and its imports (via IMPORT.imported_from) to top-level packages under the project root, and flags any internal import not in the importing package allow-list. Same-package and external imports are never flagged; packages omitted from the config are unconstrained (incremental adoption). Configured under [tool.pypeeker.import-boundaries] with an allow table and optional root.
- Fixed the tree -> binder layering leak found in the audit: module_path_from (a generic path helper, not a binding concern) moved from binder.helpers to a new dependency-free leaf module pypeeker/paths.py; all call sites updated.
- pypeeker now enforces its own layering: the rule is enabled in pyproject with the real package graph, and architecture.md documents it.

User impact: architectural boundaries are now machine-checked. A forbidden cross-package import fails pypeeker check (and CI) with a clear message, e.g. "package query may not import storage (via pypeeker.storage.IndexStore)".

Tests: 403 pass, incl. 8 new rule tests; pypeeker check exits 0; a live negative proof confirmed the rule flags a deliberately-introduced violation.

Follow-up: duplicate import-resolution logic in analysis/graph.py (audit item #1) and a move-symbol refactor remain open; this task addresses the layering-rule and the #2 leak.
<!-- SECTION:FINAL_SUMMARY:END -->
