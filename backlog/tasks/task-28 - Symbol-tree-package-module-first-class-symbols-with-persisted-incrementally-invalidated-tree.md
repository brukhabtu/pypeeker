---
id: TASK-28
title: >-
  Symbol tree: package/module first-class symbols with persisted,
  incrementally-invalidated tree
status: In Progress
assignee:
  - '@claude'
created_date: '2026-05-23 22:11'
updated_date: '2026-05-23 22:11'
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
- [ ] #1 SymbolKind.PACKAGE exists; every indexed module is emitted as a first-class MODULE Symbol (symbol_id == dotted module_path) in its FileIndex
- [ ] #2 A tree builder assembles a cross-file symbol tree: it synthesizes a PACKAGE node for every dotted prefix of every module_path and links package->subpackage->module with parent/child ids from the root package down to each module
- [ ] #3 Below the module boundary the tree composes with existing per-file structure: members(module) returns the module's top-level symbols and members(class/function) returns nested symbols via parent_scope_id, so the tree spans root package -> ... -> locals
- [ ] #4 The tree is persisted to the .semantic-tool cache as its own artifact, separate from the per-file index files, round-tripping through the existing dataclass serializer
- [ ] #5 Each package node carries a membership-aware subtree_hash computed bottom-up from its members (child module file_hashes + child package subtree_hashes + member names); adding, removing, or renaming a member changes the hashes of exactly that package and its ancestors while sibling subtrees stay byte-identical
- [ ] #6 Tree is fresh-on-read and rebuilt incrementally: on read, package subtrees whose subtree_hash is unchanged are reused and only subtrees containing added/removed/edited modules are rebuilt; a no-change read performs no rebuild work beyond hash comparison
- [ ] #7 Stale detection covers added files, removed files, and edited files (file_hash change)
- [ ] #8 The query engine exposes the tree (e.g. get_tree / document_symbols(module) / members(symbol)) returning package/module nodes with parent/child links and physical locations
- [ ] #9 Full test suite green; pypeeker check still exits 0; existing find_symbol / find_references / plan-rename behavior is unchanged
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
