---
id: TASK-31
title: 'Transitive barrel rename: update package re-export consumer imports'
status: Done
assignee:
  - '@claude'
created_date: '2026-05-23 22:50'
updated_date: '2026-05-23 23:02'
labels:
  - refactor
  - index
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Follow-up discovered while implementing TASK-30. When a definition is re-exported through a package __init__ (barrel) and a consumer imports it via the package (from pkg import X) rather than the defining module (from pkg.lib import X), rename does not update the consumer's import statement: find_import_symbols matches only the direct module path (imported_from pkg.lib.X), so the barrel consumer's import (imported_from pkg.X) is missed. TASK-30 keeps such modules internally consistent by also leaving their call sites alone, but the consumer is still left importing the old name from the barrel. Closing this requires resolver-based import discovery (find all IMPORT symbols whose resolve_definition == the canonical def, following re-export chains) and feeding those import ids into the rename binding set so both the import and its call sites cascade. DESIGN FORK to resolve first: updating a barrel consumer's 'from pkg import X' to the new name is only valid if pkg actually re-exports the new name, i.e. the __init__ re-export was also updated (today gated behind --include-exports). Decide whether transitive barrel-consumer import updates should be gated on --include-exports (treated as part of the re-export chain) or always applied, and how to detect that a given import resolved through a re-export vs directly.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 find_importers/resolver-based discovery returns all IMPORT symbols that resolve to the canonical definition, including those routed through __init__ barrels
- [x] #2 Renaming a barrel-re-exported definition (with the agreed --include-exports semantics) updates barrel-consumer import statements AND their call sites, leaving consumer modules valid and runnable
- [x] #3 The --include-exports gating decision for transitive barrel consumers is implemented and documented; direct-import and existing __init__ behavior is unchanged
- [x] #4 Tests cover a barrel-consumer end-to-end rename producing runnable code; full suite green; pypeeker check exits 0
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. RESOLVER: record resolution path so callers can tell direct vs barrel-routed. Add crosses_barrel(symbol_id) -> bool: True if any IMPORT symbol traversed during resolution lives in an __init__.py. (Refactor resolve_definition to share an internal walk that returns the chain.)
2. ENGINE: add find_importers(symbol_id) -> list[Symbol] = all IMPORT symbols whose resolve_definition == resolve_definition(symbol_id) (superset of find_import_symbols; also catches barrel consumers). Expose import_crosses_barrel via resolver.
3. PLANNER: replace find_import_symbols with find_importers for import discovery. Gating rule: an import is gated on --include-exports if it lives in an __init__.py OR its resolution path crosses a barrel (so barrel-consumer imports gate with the re-export). Direct imports always update (unchanged). binding_ids/call-site cascade unchanged (driven by imports_to_edit).
4. TESTS: barrel consumer end-to-end - flag ON updates def + __init__ re-export + barrel-consumer import + call site (runnable); flag OFF leaves consumers untouched. Keep all existing planner tests green.
5. DOCS: update architecture.md/comment to reflect implemented gated behavior; resolve TASK-31 ACs.
6. Full suite + pypeeker check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented the gated transitive barrel rename (the only sound option; the always-apply branch would import a name the barrel does not export):
- resolve.py: refactored resolution into _resolve_chain (returns every id visited). Added crosses_barrel(symbol_id): True if any IMPORT hop in the chain lives in an __init__.py.
- query/engine.py: find_importers(symbol_id) = all IMPORT symbols resolving to the same canonical def (superset of find_import_symbols; catches barrel consumers like `from pkg import X`). import_crosses_barrel passthrough.
- planner.py: import discovery now uses find_importers; gating rule = an import on the export surface (lives in __init__.py OR its resolution crosses a barrel) is gated on --include-exports; direct imports always update. Call-site cascade unchanged (binding_ids from imports_to_edit).

DESIGN FORK RESOLUTION: gate transitive barrel-consumer updates on --include-exports. Verified end-to-end: with the flag, renaming pkg.lib:make->build rewrites def + __init__ re-export + barrel consumer import + call site; `python -c "import pkg.app"` runs and prints 1. Without the flag, the barrel consumer is left untouched.

The separate alias-preserving mode (rename the def but hold the public export name) remains future work, documented in code + architecture.md.

Tests: 395 pass (new flag-on barrel test + renamed flag-off test). pypeeker check exits 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Closed the transitive barrel rename gap: with --include-exports, renaming a definition that is re-exported through a package __init__ now also updates barrel consumers (from pkg import X) — both their import statement and their call sites — so the whole re-export chain stays runnable.

What changed:
- Resolver exposes the resolution chain and crosses_barrel(): whether resolving an import passes through an __init__ re-export.
- Query engine gains find_importers (all imports resolving to a definition, including barrel-routed ones) and import_crosses_barrel.
- The rename planner discovers imports via find_importers and gates export-surface imports (those in __init__.py or whose resolution crosses a barrel) on --include-exports. Direct imports are always updated; the call-site cascade is unchanged.

Design decision: transitive barrel-consumer rewrites are gated on --include-exports because they are only sound once the re-export they depend on is updated (the always-apply alternative would import a name the barrel does not export). A separate alias-preserving mode ("rename the def but keep the public export name") remains future work and is documented in the planner and architecture.md.

User impact: `pypeeker plan-rename pkg.lib:X NewX --include-exports` now produces a complete, runnable rename across the definition, the __init__ re-export, and barrel consumers. Without the flag, barrel consumers are left untouched (not half-renamed).

Tests: 395 pass, including an end-to-end-style barrel rename (import + call site updated) and the flag-off untouched case. pypeeker check exits 0.
<!-- SECTION:FINAL_SUMMARY:END -->
