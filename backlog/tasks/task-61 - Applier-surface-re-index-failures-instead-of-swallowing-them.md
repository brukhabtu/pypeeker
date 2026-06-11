---
id: TASK-61
title: 'Applier: surface re-index failures instead of swallowing them'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 15:46'
updated_date: '2026-06-11 15:52'
labels:
  - refactor
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TransactionApplier._reindex_files catches every exception with a bare pass, so a failed re-index after a successful apply leaves the on-disk index silently inconsistent — and the rest of the system trusts the index. Failures must be reported.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Re-index failures are collected and returned in the apply JSON output (e.g. files_reindex_failed with error messages), not swallowed
- [x] #2 Apply still succeeds (edits are already on disk) but the output makes index inconsistency visible
- [x] #3 Test covers a re-index failure surfacing in the result
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Change TransactionApplier._reindex_files to collect per-file failures as {"file": path, "error": str(e)} instead of bare pass; return (reindexed, failed)
2. Include files_reindex_failed (empty list when all good) in apply() result dict, keeping existing keys unchanged
3. Add test in tests/test_applier.py monkeypatching IndexStore.save to raise, asserting status stays "applied" and the failure appears in files_reindex_failed
4. Run uv run pytest -q to verify no regressions
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Changed _reindex_files to return (reindexed, failed) where failed is a list of {"file": path, "error": str(e)} entries instead of swallowing exceptions with bare pass
- apply() result dict now includes files_reindex_failed (empty list when all re-indexes succeed); existing keys (tx_id, status, files_modified, files_reindexed) unchanged
- Added TestReindexFailures in tests/test_applier.py: monkeypatches IndexStore.save to raise, asserts status stays "applied" while failure surfaces in files_reindex_failed; second test verifies partial failure (one file fails, other reindexes fine)
- Also added a success-path assertion that files_reindex_failed == [] on a clean apply
- Full suite: uv run pytest -q -> 495 passed, 10 skipped
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Surfaced re-index failures from TransactionApplier instead of swallowing them.

Changes:
- `_reindex_files` (src/pypeeker/refactor/applier.py) no longer catches exceptions with a bare `pass`; it now returns `(reindexed, failed)` where each failure is recorded as `{"file": path, "error": str(e)}`.
- `apply()` includes a new `files_reindex_failed` key in its result dict (empty list when all re-indexes succeed). Existing keys (`tx_id`, `status`, `files_modified`, `files_reindexed`) are unchanged for compatibility. The apply itself still succeeds since edits are already on disk, but the JSON output now makes any index inconsistency visible.

Why: a failed re-index after a successful apply previously left the on-disk index silently stale, corrupting every downstream query and plan that trusts the index.

Tests:
- New `TestReindexFailures` in tests/test_applier.py: monkeypatches `IndexStore.save` to raise, asserting status stays "applied" while the failure appears in `files_reindex_failed`; a second test verifies partial failure (one file fails, the other still re-indexes).
- Success-path assertion that `files_reindex_failed == []` on a clean apply.
- Full suite: `uv run pytest -q` -> 495 passed, 10 skipped.
<!-- SECTION:FINAL_SUMMARY:END -->
