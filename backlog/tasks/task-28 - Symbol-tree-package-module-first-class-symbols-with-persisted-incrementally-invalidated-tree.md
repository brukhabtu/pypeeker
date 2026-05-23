---
id: TASK-28
title: >-
  Symbol tree: package/module first-class symbols with persisted,
  incrementally-invalidated tree
status: Done
assignee:
  - '@claude'
created_date: '2026-05-23 22:11'
updated_date: '2026-05-23 22:30'
labels:
  - architecture
  - binder
  - index
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Chunk 2 of the layered rebuild. Today the module exists only as a Scope (scope_id==module_path) and there are no package nodes; symbols below the module are already navigable because scope_id==symbol_id for classes/functions and parent_scope_id chains up to module_path. This task makes packages and modules first-class symbols and assembles ONE cross-file symbol tree from the root package down to locals, persisted in the .semantic-tool cache as its own artifact with membership-aware subtree-hash invalidation so reads are fresh and rebuilt incrementally. Unblocks Chunk 3 (cross-module resolution).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 SymbolKind.PACKAGE exists; every indexed module is emitted as a first-class MODULE Symbol (symbol_id == dotted module_path) in its FileIndex
- [x] #2 A tree builder assembles a cross-file symbol tree: it synthesizes a PACKAGE node for every dotted prefix of every module_path and links package->subpackage->module with parent/child ids from the root package down to each module
- [x] #3 Below the module boundary the tree composes with existing per-file structure: members(module) returns the module's top-level symbols and members(class/function) returns nested symbols via parent_scope_id, so the tree spans root package -> ... -> locals
- [x] #4 The tree is persisted to the .semantic-tool cache as its own artifact, separate from the per-file index files, round-tripping through the existing dataclass serializer
- [x] #5 Each package node carries a membership-aware subtree_hash computed bottom-up from its members (child module file_hashes + child package subtree_hashes + member names); adding, removing, or renaming a member changes the hashes of exactly that package and its ancestors while sibling subtrees stay byte-identical
- [x] #6 Tree is fresh-on-read and rebuilt incrementally: on read, package subtrees whose subtree_hash is unchanged are reused and only subtrees containing added/removed/edited modules are rebuilt; a no-change read performs no rebuild work beyond hash comparison
- [x] #7 Stale detection covers added files, removed files, and edited files (file_hash change)
- [x] #8 The query engine exposes the tree (e.g. get_tree / document_symbols(module) / members(symbol)) returning package/module nodes with parent/child links and physical locations
- [x] #9 Full test suite green; pypeeker check still exits 0; existing find_symbol / find_references / plan-rename behavior is unchanged
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. MODEL: add SymbolKind.PACKAGE. Add TreeNode {symbol_id, name, kind(PACKAGE|MODULE), file_path?, file_hash?, subtree_hash, parent_id?, children: list[str]} and TreeIndex {nodes: dict[str,TreeNode] or list, root_ids} in models/tree.py.
2. BINDER: in visit_module, also emit a MODULE Symbol (symbol_id==module_path, kind=MODULE, location=module span, parent_scope_id=None) into state.symbols so the module is first-class in the FileIndex.
3. BUILDER (pypeeker/tree.py): build_tree(store) loads all FileIndexes -> module nodes (module_path, file_path, file_hash); synthesize PACKAGE nodes for every dotted prefix; link parent/child; compute subtree_hash bottom-up (sha256 over sorted child entries: module->name:file_hash, package->name:subtree_hash).
4. STORE (storage/tree_store.py): save/load tree.json under .semantic-tool via existing serializer.
5. INVALIDATION: load_or_rebuild(store) compares persisted module set+hashes vs current FileIndexes; rebuild only subtrees whose recomputed subtree_hash differs, reuse unchanged package nodes; handle add/remove/edit. No-change read = hash compare only.
6. QUERY: SemanticQueryEngine.get_tree()/document_symbols(module)/members(symbol) composing tree (>=module) with FileIndex parent_scope_id walk (<module).
7. WIRE: indexer/cli rebuild or refresh tree after indexing.
8. TESTS: package/module symbols; tree shape root->locals; persistence round-trip; subtree-hash locality (edit one module -> only its ancestors change, siblings byte-identical); add/remove/edit invalidation; check exits 0; rename/find unchanged.
9. Run full suite + pypeeker check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented across model/binder/builder/store/query/cli:
- models/symbols.py: added SymbolKind.PACKAGE.
- binder/binder.py: visit_module now emits a first-class MODULE Symbol (symbol_id==module_path==module scope id, parent_scope_id=None, kept out of scope.symbol_ids so resolution/visibility queries are unchanged); captures the module docstring.
- models/tree.py: TreeNode/TreeIndex dataclasses (serialize for free via the existing dataclass serializer; dict[str,TreeNode] supported by _coerce).
- tree.py: build_tree synthesizes a PACKAGE node per dotted prefix, links parent/child, computes membership-aware subtree_hash bottom-up (name + own file_hash + sorted child name=hash). A package backed by __init__.py collapses onto the same node as its prefix (file_path set + children). load_or_rebuild: no-change fast path via file->hash manifest compare (zero node reconstruction); otherwise reuses cached subtrees whose subtree_hash is unchanged and only persists changed/removed subtrees; reports rebuilt/reused/removed.
- storage/tree_store.py: persists .semantic-tool/tree.json.
- query/engine.py: get_tree(), document_symbols(module), members(symbol) composing tree (>=module) with FileIndex parent_scope_id walk (<module).
- cli.py: index now refreshes the tree; new `tree [symbol_id]` command.

Notes:
- Rewrote two walrus-in-comprehension expressions in tree.py as plain loops because pypeeker check (dogfooding no-unresolved-refs) flagged the walrus targets as unresolved.
- Updated test_binder empty/comment-only assertions to exclude the new MODULE symbol.
- Pre-existing ruff F821 on engine.py:96 (find_reexport_locations -> list[Location]) is unrelated and left as-is.

Verification: 378 tests pass (22 new in tests/test_tree.py); `pypeeker index src` + `pypeeker check` exits 0; `pypeeker tree` / `pypeeker tree pypeeker.storage` render the package->module->symbol spine.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Made packages and modules first-class symbols and assembled one cross-file symbol tree, persisted with membership-aware, incrementally-invalidated subtree hashes (Chunk 2 of the layered rebuild).

What changed:
- Added SymbolKind.PACKAGE; the binder now emits a first-class MODULE symbol per file (symbol_id == dotted module_path, matching the module scope id), so the module joins the symbol space without disturbing name resolution or visible-symbol queries.
- New TreeNode/TreeIndex model + build_tree(): synthesizes a PACKAGE node for every dotted prefix and links package -> subpackage -> module from the root down. A package backed by __init__.py collapses onto the same node as its prefix.
- Each node carries a subtree_hash derived bottom-up from its own source hash + sorted child hashes; adding/removing/renaming/editing a member changes only that node and its ancestors, leaving sibling subtrees byte-identical.
- Persisted to .semantic-tool/tree.json (TreeStore) via the existing dataclass serializer.
- load_or_rebuild keeps the tree fresh-on-read: a file->hash manifest match short-circuits to the cached tree (no reconstruction); otherwise unchanged subtrees are reused and only changed/removed subtrees are rebuilt and persisted. Handles added, removed, and edited files.
- Query surface: get_tree(), document_symbols(module), members(symbol) — composing the tree (at/above module) with the per-file parent_scope_id walk (below module), so navigation spans root package -> ... -> locals. New `pypeeker tree [symbol_id]` CLI command; `pypeeker index` refreshes the tree.

User impact: callers can now walk the whole project structure (packages, modules, classes, functions, locals) from one tree, and the persisted artifact only re-derives the parts that actually changed.

Tests: 378 pass, incl. 22 new in tests/test_tree.py covering module symbols, tree shape, persistence round-trip, subtree-hash locality, and add/remove/edit/no-change invalidation. `pypeeker check` exits 0 on the repo.

Follow-ups/risks: cross-module reference resolution builds on this in Chunk 3. Pre-existing ruff F821 on engine.py:96 is unrelated and untouched.
<!-- SECTION:FINAL_SUMMARY:END -->
