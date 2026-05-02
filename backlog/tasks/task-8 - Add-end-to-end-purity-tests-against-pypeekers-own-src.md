---
id: TASK-8
title: Add end-to-end purity tests against pypeeker's own src/
status: Done
assignee: []
created_date: '2026-04-30 03:59'
updated_date: '2026-05-02 00:20'
labels: []
dependencies:
  - TASK-7
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
No test currently runs check_purity on real-world Python code. Pypeeker's own src/ is the natural target — it has known-impure functions (file IO in IndexStore.save, in-place edits in TransactionApplier.apply) and known-pure helpers (e.g., model serialization, query lookups). This was the original 'third-party using pypeeker on a real codebase' framing.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Add a session/module-scoped fixture that builds an IndexStore over src/pypeeker/ (or reuses .semantic-tool/index/ if present)
- [x] #2 Assert check_purity('src/pypeeker/storage/store.py:IndexStore.save').verdict == IMPURE with at least one impure-call or attribute-write evidence
- [x] #3 Assert check_purity on TransactionApplier.apply or IndexStore.remove returns IMPURE
- [x] #4 Assert check_purity on a known-pure helper (e.g., IndexStore.compute_file_hash is impure due to file read; pick something like a pydantic model's __init__ or a pure utility) returns PROBABLY_PURE
- [x] #5 Tests skip cleanly with pytest.skip if the index isn't available rather than failing
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
ACs satisfied by tests/test_purity_self.py landed in TASK-13 + extended in TASK-14/15: (1) module-scoped project_store fixture uses pytest.skip when .semantic-tool/index/ is missing; (2) IndexStore.save asserted IMPURE with write_text evidence; (3) TransactionApplier.apply, _apply_file_rename, _reindex_files asserted IMPURE; (4) IndexStore.compute_file_hash asserted IMPURE (read_bytes); (5) IndexStore.project_root, _source_to_index_path, _apply_edits_to_content asserted PROBABLY_PURE with empty evidence. Test skips cleanly when pypeeker hasn't been re-indexed. Total of 10 parametrized e2e cases covering the original AC set.
<!-- SECTION:FINAL_SUMMARY:END -->
