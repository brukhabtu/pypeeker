---
id: TASK-8
title: Add end-to-end purity tests against pypeeker's own src/
status: To Do
assignee: []
created_date: '2026-04-30 03:59'
updated_date: '2026-04-30 04:03'
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
- [ ] #1 Add a session/module-scoped fixture that builds an IndexStore over src/pypeeker/ (or reuses .semantic-tool/index/ if present)
- [ ] #2 Assert check_purity('src/pypeeker/storage/store.py:IndexStore.save').verdict == IMPURE with at least one impure-call or attribute-write evidence
- [ ] #3 Assert check_purity on TransactionApplier.apply or IndexStore.remove returns IMPURE
- [ ] #4 Assert check_purity on a known-pure helper (e.g., IndexStore.compute_file_hash is impure due to file read; pick something like a pydantic model's __init__ or a pure utility) returns PROBABLY_PURE
- [ ] #5 Tests skip cleanly with pytest.skip if the index isn't available rather than failing
<!-- AC:END -->
