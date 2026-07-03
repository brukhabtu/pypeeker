---
id: TASK-104
title: 'check: add barrel-only import rule and enable it repo-wide'
status: Done
assignee:
  - '@claude'
created_date: '2026-07-03 01:57'
updated_date: '2026-07-03 02:24'
labels:
  - architecture
  - check
dependencies:
  - TASK-103
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Consumers can bypass a package's curated public surface by deep-importing its internal submodules (from pypeeker.refactor.planner import X instead of from pypeeker.refactor import X). Now that query/refactor/models barrels exist, add an opt-in 'barrel-only' rule to pypeeker check that flags a cross-package import of an internal submodule when the target package exposes a curated barrel, and enable it on this repo. Complements import-boundaries: that rule governs which packages may depend on which; this one governs that they depend via the public surface.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 New rule flags cross-package imports of a package's internal submodules when that package's __init__ defines __all__; same-package and barrel imports are never flagged
- [x] #2 Rule is opt-in via [tool.pypeeker] rules and has unit tests covering: deep import flagged, barrel import clean, same-package deep import clean, package without a curated barrel not flagged
- [x] #3 Enabled in this repo's pyproject and the self-check (index src && check) passes
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add a project-scoped 'barrel-only' rule in check/builtin/. For each IMPORT symbol whose importing file is in package P_f, if it deep-imports a submodule of a DIFFERENT package P_t (imported_from module path is P_t.<sub>... not P_t itself), and P_t's __init__ barrel re-exports the same canonical definition (compare via CrossModuleResolver.resolve_definition against the barrel's re-export symbols), flag it: 'import X via the pypeeker.<P_t> barrel, not its internal module'. Never flag same-package imports or packages whose barrel does not expose the name.
2. Unit tests: deep import flagged, barrel import clean, same-package deep import clean, package without curated barrel not flagged, deep import of a name NOT in the barrel not flagged.
3. Enable [tool.pypeeker] rules += barrel-only; migrate the resulting flagged deep imports (e.g. treebuild storage imports) to their barrels so the self-check passes.
4. Gate: pytest, ruff, index+check exit 0.
<!-- SECTION:PLAN:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added the 'barrel-only' project-scoped check rule and enabled it repo-wide.

The rule flags a cross-package import that reaches past a package's curated __init__ barrel into an internal submodule when the same canonical definition is re-exported by that barrel (e.g. 'from pypeeker.refactor.planner import RenamePlanner' when 'from pypeeker.refactor import RenamePlanner' works). It reuses the CrossModuleResolver origin-resolution that import-boundaries uses: a package is a curated barrel when its __init__ declares __all__, and the flag fires only when resolve_definition(import) is in that barrel's re-exported definitions. Never flags same-package imports, packages without a curated barrel, names the barrel does not export, or dynamic/synthetic imports.

Complements import-boundaries (which package may depend on which) with the orthogonal 'go through the public surface' constraint. Enabled in [tool.pypeeker] rules with root wired via [tool.pypeeker.barrel-only]. Migrated ~15 flagged deep imports (treebuild/check/refactor/indexer/app/cli) to their storage/analysis/binder barrels; where a line mixed a barrel name with a genuinely-internal one, split it so only the internal deep import remains.

Tests: 8 new TestBarrelOnly cases (flagged/clean/same-package/no-barrel/name-not-exported/dynamic-ignored/root-fallback/enabled-in-repo). 1391 passed; ruff clean; index src && check exit 0.
<!-- SECTION:FINAL_SUMMARY:END -->
