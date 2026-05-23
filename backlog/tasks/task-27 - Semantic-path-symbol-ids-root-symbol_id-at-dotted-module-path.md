---
id: TASK-27
title: 'Semantic-path symbol ids: root symbol_id at dotted module path'
status: Done
assignee:
  - '@claude'
created_date: '2026-05-23 17:23'
updated_date: '2026-05-23 17:34'
labels:
  - binder
  - architecture
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Keystone of the layered-architecture rebuild. Change symbol_id from file-path-rooted (src/pypeeker/analysis/calls.py:bare_calls) to dotted-module-path-rooted (pypeeker.analysis.calls:bare_calls). location keeps the physical file:line:col. No package tree yet (Chunk 2). Unblocks cross-module resolution and the whole identify/analyze/rule stack.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A path->module-path mapper converts a source file + configured src roots to a dotted module path (src/pypeeker/analysis/calls.py -> pypeeker.analysis.calls)
- [x] #2 src roots are available as general project config consumed by the indexer, not only by check
- [x] #3 BinderState carries both module_path (for ids) and file_path (for locations); bind() threads module_path through
- [x] #4 build_symbol_id/build_scope_chain root ids at module_path; the ':' separates module path from in-module scope chain
- [x] #5 <builtins>. and <unresolved>. synthetic ids are unaffected
- [x] #6 indexer computes module_path from the resolved file + src roots and passes it to bind
- [x] #7 All id assertions in tests updated to dotted-module form; full suite green
- [x] #8 pypeeker symbol/refs return dotted-module ids with physical locations; pypeeker check still exits 0; plan-rename still resolves
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented module_path_from() in binder/helpers.py (strips src roots, drops .py, collapses __init__). Added project.py:load_src_roots() as general project config; indexer + applier reindex both use it. BinderState carries module_path (id root) and file_path (location). bind() takes optional module_path, defaulting to module_path_from(file_path). build_symbol_id/build_scope_chain/build_symbol_id_for_scope param renamed file_path->id_root; all binder id sites pass state.module_path while locations keep state.file_path. graph._resolve_import_target simplified to a direct dotted-module join (pkg.mod.func -> pkg.mod:func) now that imported_from matches the id scheme. ~260 id assertions across 17 test files mechanically rewritten (path.py:ident -> dotted:ident; f-strings handled; purity_self uses real src-stripped ids). Full suite 361 passed. End-to-end: pypeeker symbol/refs return dotted ids with physical locations; check exits 0; intra-module refs resolve (cross-module refs to imports remain a Chunk-3 item). No backwards-compat shims.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Root symbol ids at the dotted module path instead of the file path — the keystone of the layered-architecture rebuild.

## What changed
- New `module_path_from(path, src_roots)` in `binder/helpers.py`: `src/pypeeker/analysis/calls.py` -> `pypeeker.analysis.calls`, collapsing `__init__` to its package.
- New `pypeeker/project.py::load_src_roots()` — `[tool.pypeeker].src` promoted to general project config consumed by the indexer (and the refactor reindex), not just `check`.
- `BinderState` now carries both `module_path` (id root) and `file_path` (physical, for `location`). `bind()` accepts an optional `module_path`, defaulting to `module_path_from(file_path)` for inline/test sources; the indexer passes the project-aware path.
- `build_symbol_id` / `build_scope_chain` / `build_symbol_id_for_scope` param renamed `file_path` -> `id_root`; every binder id site passes `state.module_path`, while `make_location` / `Scope.file_path` keep `state.file_path`.
- `analysis/graph.py::_resolve_import_target` reduced to a direct join — `imported_from` (`pkg.mod.func`) now maps straight to `pkg.mod:func` since the id scheme matches. (Foundation for Chunk 3 cross-module resolution.)

## Result
ids are now `pypeeker.analysis.calls:BareCall.method`; `location` still carries `file:line:col`. The two axes (semantic vs physical) are now distinct, as the locked `{id, location}` handle intends.

## Tests
~260 id assertions across 17 test files rewritten mechanically (`path.py:ident` -> `dotted:ident`). Full suite: 361 passed. End-to-end on the project itself: `pypeeker symbol`/`refs` return dotted ids + physical locations, `pypeeker check` exits 0, intra-module refs resolve. (Cross-module refs to imported names still bind to the import symbol — addressed in Chunk 3.)

No backwards-compatibility shims: ids changed outright.
<!-- SECTION:FINAL_SUMMARY:END -->
