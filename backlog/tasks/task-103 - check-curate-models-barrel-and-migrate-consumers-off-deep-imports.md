---
id: TASK-103
title: 'check: curate models barrel and migrate consumers off deep imports'
status: Done
assignee:
  - '@claude'
created_date: '2026-07-03 01:57'
updated_date: '2026-07-03 02:10'
labels:
  - architecture
  - refactor
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The models package __init__ is an empty barrel, so ~150 in-repo sites deep-import internal modules (from pypeeker.models.symbols import ...). models has no declared public surface. Curate it like the storage/check/query/refactor barrels and migrate src/ consumers to import via the barrel. Deferred during the architecture review due to blast radius; do it as its own change.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 models/__init__.py re-exports the public surface with an explicit __all__, matching the style of storage/__init__.py
- [x] #2 All src/pypeeker consumers outside models/ import model types via 'from pypeeker.models import X' (tests may keep deep imports)
- [x] #3 uv run pytest, uv run ruff check src tests, and uv run pypeeker index src && uv run pypeeker check all pass
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Build models/__init__.py barrel re-exporting the public surface of all 10 submodules (capabilities, index, location, references, scopes, serialize, symbol_id, symbols, transaction, tree) with an explicit __all__, matching storage/__init__.py style.
2. Migrate all 152 deep-import sites in src/pypeeker outside models/ to 'from pypeeker.models import X'.
3. Leave tests deep-importing internals as-is.
4. Gate: pytest, ruff check src tests, pypeeker index src && check (strict) all pass.
<!-- SECTION:PLAN:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Curated the models package barrel and migrated all in-repo consumers to it.

Changes:
- models/__init__.py now re-exports the full public surface of all 10 submodules (35 names) with a sorted __all__, matching the storage/check barrel style. Each name is re-exported only from its true defining submodule (no double re-export of cross-imported names like Confidence/Location/SymbolKind).
- Migrated 152 deep-import statements across 55 files under src/pypeeker (cli, app, check, refactor, binder, analysis, query, resolve, treebuild, storage, adapters) from 'from pypeeker.models.<submodule> import X' to 'from pypeeker.models import X', collapsing contiguous same-scope imports. Tests keep their deep imports by design.

Impact: models now has a declared public surface; consumers depend on it rather than internal module paths. Unblocks TASK-104 (barrel-only rule).

Tests: 1383 passed; ruff clean; pypeeker index src && check exit 0 (import-boundaries strict clean).
<!-- SECTION:FINAL_SUMMARY:END -->
