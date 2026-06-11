---
id: TASK-86
title: 'storage: overlay IndexStore (in-memory VFS for simulation)'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 18:27'
updated_date: '2026-06-11 18:57'
labels:
  - storage
  - m3-planner
dependencies: []
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The composite planner simulates whole fix pipelines without touching disk. Add an overlay store: {path: bytes} layered over the real tree; reads prefer the overlay; binding runs against overlay content; indexes for overlaid files are bound in-memory. Pure bind() makes this cheap.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 OverlayIndexStore (or equivalent) supports read/write/delete of overlaid file bytes plus load/save/is_stale/list semantics consistent with IndexStore
- [x] #2 Binding a mutated overlay file yields a correct in-memory FileIndex without disk writes; underlying store and disk remain untouched
- [x] #3 Query engine and resolver work unchanged on an overlay store (read-through contract); tests cover layering, mutation, and isolation
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Survey IndexStore contract + consumers (engine/resolver/context/planners): project_root, load, save, remove, is_stale, list_indexed_files, compute_file_hash.
2. Add src/pypeeker/storage/overlay.py: OverlayIndexStore wrapping a base IndexStore (composition). File-bytes layer (write_file/delete_file/read_file, overlay-first then disk); in-memory index layer (save/load/remove on a dict, read-through to base; never writes disk or mutates base); is_stale hashes overlay content; list_indexed_files = base + additions - removals.
3. rebind cannot live in storage (import-boundaries: storage=[models] only) and indexer._index_file is disk-coupled (reads bytes from disk), so place rebind in a NEW thin helper src/pypeeker/refactor/simulate.py (refactor may import adapters/binder/paths/project/storage) mirroring _index_file parse/bind/save over overlay bytes. OverlayIndexStore stays pure storage.
4. Export OverlayIndexStore from storage/__init__.py.
5. tests/test_overlay_store.py: layering, save/load/remove isolation (byte-compare .semantic-tool before/after), is_stale transitions across write_file -> rebind, list semantics, SemanticQueryEngine smoke test over overlay vs real store.
6. uv run pytest -q green; ruff clean on new files.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Surveyed the store contract used by consumers (engine/resolver/context/planners/check/treebuild): project_root, load, save, remove, is_stale, list_indexed_files, compute_file_hash. OverlayIndexStore satisfies all of them via composition over a base IndexStore.
- src/pypeeker/storage/overlay.py: file-bytes layer (write_file/delete_file/read_file/file_exists, overlay-first then disk under base.project_root, tombstones for deletions) + in-memory index layer (save/load/remove on dicts, read-through to base; save returns the virtual index path without writing). is_stale hashes overlay-visible content (sha256, same as binder compute_hash) against the loaded index hash; list_indexed_files = base + additions - removals.
- Placement decision for rebind: storage may only import models (import-boundaries), and indexer._index_file is disk-coupled (file_path.read_bytes() + IndexResult reporting), so it cannot serve overlay bytes. rebind lives in NEW thin helper src/pypeeker/refactor/simulate.py (refactor may import adapters/binder/paths/project/storage), mirroring _index_file parse->bind->save over OverlayIndexStore.read_file content. OverlayIndexStore stays pure storage.
- Exported OverlayIndexStore from storage/__init__.py.
- tests/test_overlay_store.py (18 tests): layering, save/load/remove isolation (byte-compare .semantic-tool before/after; base.load identity preserved), is_stale transitions across write_file -> rebind, list semantics with adds/deletes, rebind correctness, and SemanticQueryEngine smoke tests (overlay engine sees mutated+virtual symbols and resolves references; real-store engine does not).
- Verification: uv run pytest -q -> 802 passed; ruff clean on new files; uv run pypeeker check -> exit 0 (import boundaries + docstrings hold).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added OverlayIndexStore, an in-memory VFS layered over the real IndexStore, so the composite batch planner (TASK-88) can simulate whole fix pipelines without touching disk.

Changes:
- src/pypeeker/storage/overlay.py: OverlayIndexStore wraps a base IndexStore (composition) and satisfies the full consumer-facing store surface (project_root, load, save, remove, is_stale, list_indexed_files, compute_file_hash). File-bytes layer: write_file/delete_file/read_file (+file_exists) read overlay-first, then disk; deletions are tombstoned. Index layer: save/load/remove operate on in-memory dicts with read-through to the base store; save returns the virtual index path without writing. is_stale hashes overlay-visible content, so write_file makes a path stale until rebind. list_indexed_files = base + overlay additions - removals. Nothing writes to disk or mutates the base store.
- src/pypeeker/refactor/simulate.py (new): rebind(store, path, *, adapter=None, src_roots=None) parses overlay content and saves the FileIndex in-memory. Placed under refactor (not storage) because import-boundaries restrict storage to ["models"], and indexer._index_file is disk-coupled so it cannot be reused for overlay bytes; rebind mirrors its parse->bind->save sequence over read_file content.
- src/pypeeker/storage/__init__.py: exports OverlayIndexStore.

Tests (tests/test_overlay_store.py, 18 tests):
- Layering: overlay shadows disk, non-overlaid paths pass through, delete masks disk, write-after-delete resurrects.
- Isolation: byte-compare of .semantic-tool before/after overlay save/rebind/remove; base store still serves originals.
- is_stale transitions across write_file -> rebind; unindexed/deleted paths read stale.
- list semantics with adds/removes; rebind binds mutated content correctly.
- Engine smoke: SemanticQueryEngine over the overlay sees symbols added via write_file+rebind (including a purely virtual file) and resolves references through them, while the engine over the real store does not.

Verification: uv run pytest -q -> 802 passed; ruff clean; pypeeker check (require-docstrings, no-unresolved-refs, import-boundaries) exits 0. No engine/resolver changes were needed - they work unchanged through the existing store read-through contract.
<!-- SECTION:FINAL_SUMMARY:END -->
