---
id: TASK-29
title: >-
  Cross-module resolution: resolve imports and re-exports to canonical
  definitions
status: Done
assignee:
  - '@claude'
created_date: '2026-05-23 22:34'
updated_date: '2026-05-23 22:37'
labels:
  - architecture
  - binder
  - index
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Chunk 3 of the layered rebuild, building on the symbol tree (TASK-28). Today a reference to an imported name binds to the LOCAL import symbol (e.g. consumer:IndexStore), never to the definition it came from, and find_import_symbols misses barrel re-exports (from pkg import X where pkg/__init__ re-exports X from pkg.sub). This task adds a cross-module resolver that, using the tree's module->FileIndex map, follows an import (and transitive __init__ re-export chains) to the canonical definition symbol id, and exposes cross-module 'find all usages' that reaches references made through import aliases and barrels. Purely additive: find_references, find_import_symbols, and rename behavior are unchanged.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A CrossModuleResolver maps an IMPORT symbol to the canonical definition symbol id using the tree's module->index map; module imports (import pkg.mod) resolve to the module node, from-imports resolve to the named symbol in the defining module
- [x] #2 Re-export chains through __init__.py barrels are followed transitively (from pkg import X -> pkg/__init__ re-exports from pkg.sub import X -> resolves to pkg.sub:X), with a cycle guard for circular re-exports
- [x] #3 resolve_definition(symbol_id) returns the canonical definition for any symbol id: it follows IMPORT->definition (transitively) and is idempotent for non-imports and for external/stdlib imports (os, click) which resolve to themselves
- [x] #4 find_all_references(symbol_id) returns every reference across the project that ultimately binds to the given definition, including references made via import aliases and barrel re-exports, in addition to direct references
- [x] #5 The query engine exposes resolve_definition and find_all_references; the refs CLI command gains a flag to follow imports across modules
- [x] #6 Full suite green; pypeeker check exits 0; existing find_symbol / find_references / find_import_symbols / plan-rename behavior is unchanged
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. RESOLVER (pypeeker/resolve.py): CrossModuleResolver(indexes). Build {symbol_id->Symbol} and {module_path->FileIndex} maps. resolve_definition(symbol_id): if symbol is not IMPORT return it; else parse imported_from -> if it is a known module return it (module import); else rsplit into (module, name), look up symbol named `name` with parent_scope_id==module, recurse; cycle guard via visited set; external/missing -> return input id unchanged. Cache results.
2. find_all_references(definition_id): canonical=resolve_definition(definition_id); scan all references, group by resolve_definition(ref.symbol_id)==canonical; return matches (direct + alias + barrel).
3. QUERY: SemanticQueryEngine.resolve_definition + find_all_references reusing _load_all_indexes; lazily build resolver.
4. CLI: refs --all/--follow-imports flag uses find_all_references.
5. TESTS tests/test_resolve.py: direct from-import, aliased import, bare module import, barrel re-export chain, circular re-export guard, external import idempotent, find_all_references across modules + via barrel. Plus engine/cli smoke.
6. Run full suite + pypeeker check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented:
- pypeeker/resolve.py: CrossModuleResolver(indexes) builds symbol/module/module-name maps from all FileIndexes. resolve_definition follows IMPORT->definition iteratively with a visited-set cycle guard: module imports (imported_from in known modules) resolve to the module symbol id; from-imports rsplit into (module, name) and look up the module-level symbol, recursing through __init__ barrel re-exports; external/stdlib/unindexed and unknown ids resolve to themselves. Results cached.
- find_all_references(def_id): canonicalizes def_id then returns every reference whose resolved canonical == it (direct + alias + barrel).
- query/engine.py: resolve_definition + find_all_references (lazy resolver over _load_all_indexes).
- cli.py: refs --all follows imports.

Verification on pypeeker itself: `refs pypeeker.storage.index_store:IndexStore` (exact) = 0; `refs --all` = 6 usages across cli/applier/planner, all imported via the `from pypeeker.storage import IndexStore` barrel and followed through the __init__ re-export to the canonical class def. Demonstrates the additive cross-module reach.

Tests: 390 pass (12 new in tests/test_resolve.py: from-import, alias, bare module import, barrel chain, circular-reexport termination, external idempotence, unknown idempotence, find_all_references across modules + via barrel + engine integration). pypeeker check exits 0.

Left pre-existing unrelated ruff F821 on engine.py (find_reexport_locations -> list[Location]) untouched.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added cross-module resolution (Chunk 3 of the layered rebuild), so imports and barrel re-exports resolve to the definitions they actually point at.

What changed:
- New pypeeker/resolve.py: CrossModuleResolver. resolve_definition(symbol_id) follows an IMPORT through imported_from to the canonical definition id, transitively across __init__.py re-export chains, using the dotted module paths from the symbol tree. Module imports resolve to the module node; external/stdlib and unknown ids resolve to themselves; a visited-set guards circular re-exports. Results are cached.
- find_all_references(symbol_id): returns every reference across the project that canonicalizes to the same definition — reaching usages made via import aliases and barrel re-exports, not just exact symbol-id matches.
- Query engine exposes resolve_definition and find_all_references (lazily built resolver); `pypeeker refs --all` follows imports across modules.

User impact: "find all usages of this definition" now works across files even when callers import under an alias or through a package __init__ barrel. On pypeeker itself, `refs --all pypeeker.storage.index_store:IndexStore` finds 6 usages the exact-match `refs` misses (all routed through `from pypeeker.storage import IndexStore`).

Purely additive: find_references, find_import_symbols, and plan-rename are unchanged.

Tests: 390 pass (12 new in tests/test_resolve.py). pypeeker check exits 0.

Follow-ups/risks: attribute access on a bare module import (m.foo) is not resolved to m's member yet; rename could later adopt find_all_references to update aliased call sites. Pre-existing unrelated ruff F821 in engine.py left untouched.
<!-- SECTION:FINAL_SUMMARY:END -->
