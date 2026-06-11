---
id: TASK-65
title: 'Transactions CLI: list, show, cancel, and rollback'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 15:47'
updated_date: '2026-06-11 16:17'
labels:
  - cli
  - refactor
dependencies:
  - TASK-64
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The architecture doc promises plan/validate/execute/rollback, but there is no CLI to list pending transactions (TransactionStore.list() exists, unused), inspect one, cancel one, or roll back an applied one — even though EditEntry stores the old text precisely to enable rollback. Close the transaction lifecycle loop.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 pypeeker transactions list/show/cancel commands exist with JSON output
- [x] #2 A rollback command restores pre-apply content using stored old values, with hash verification against the post-apply state and re-indexing of affected files
- [x] #3 Command help documents the lifecycle; tests cover list/cancel/rollback round-trip
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add a RollbackError + TransactionRollback logic in refactor/applier.py: load tx, require status APPLIED, replay edits per file to derive post-apply spans (ascending order, cumulative delta), verify each span holds the new text, then splice back old text bottom-to-top; reverse file rename (new_path -> old_path); mark ROLLED_BACK; re-index via existing _reindex_files helper, return result dict with files_restored/files_reindexed/files_reindex_failed. Refuse with clear error on any mismatch (no partial rollback).
2. CLI in cli.py: a `transactions` click group with subcommands list/show/cancel (JSON via json.dumps(indent=2), errors as {"error": ...} + exit 1), and a top-level `rollback TX_ID` command mirroring `apply`. cancel only deletes PENDING transactions.
3. Tests: extend tests/test_applier.py with rollback round-trip (PENDING->APPLIED->ROLLED_BACK, byte-identical restore), refusal cases (PENDING, already ROLLED_BACK, post-apply modification), rename reversal; new tests/test_transactions_cli.py with CliRunner covering list/show/cancel/rollback incl. error paths and help text.
4. Run uv run pytest -q.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Added TransactionApplier.rollback() + _revert_edits_in_content in refactor/applier.py: APPLIED-only, post-apply span verification with cumulative-delta offset replay, restored-bytes hash check against plan-time file_hash, atomic temp+swap writes, rename reversal, re-index, status ROLLED_BACK. Reused existing RollbackError.
- Added CLI: `transactions` group (list/show/cancel) + top-level `rollback` command in cli.py; group help documents the lifecycle. Cancel refuses non-PENDING.
- Next: tests.

- Tests added: TestRollback in tests/test_applier.py (11 cases: byte-identical round trip, length-changing offsets, re-index, rename reversal, refusals for PENDING/ROLLED_BACK/FAILED/not-found/modified-span/modified-outside-span, terminal ROLLED_BACK) and new tests/test_transactions_cli.py (18 cases: help/lifecycle docs, list empty+shape+status transitions, show + not-found, cancel pending + refuse applied + not-found, rollback round trip + refusals).
- Updated stale "(future) rollback command" docstring in transaction_store.update_status; no functional store changes needed.
- Full suite: 595 passed, 10 skipped, 0 failures (uv run pytest -q).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Closed the transaction lifecycle loop: added a `transactions` CLI group (list/show/cancel), a top-level `rollback` command, and the rollback engine itself.

Changes:
- src/pypeeker/refactor/applier.py: new `TransactionApplier.rollback(tx_id)` + `_revert_edits_in_content` helper. Only APPLIED transactions roll back. Post-apply spans are re-derived by replaying edits in ascending order with a cumulative byte delta; each span must still hold the replacement text AND the restored bytes must hash back to the plan-time file hash — any mismatch refuses the whole rollback before a single byte is written (no partial rollback). Restores via temp-file + atomic swap, reverses a file rename (new_path -> old_path, also verified by hash for rename-only files), re-indexes affected files via the shared `_reindex_files` helper (result includes files_reindexed and files_reindex_failed, mirroring apply), and marks the transaction ROLLED_BACK (terminal: re-apply is refused).
- src/pypeeker/cli.py: `transactions` click group whose help documents the PENDING -> APPLIED/FAILED -> ROLLED_BACK lifecycle; `transactions list` emits tx_id/operation/status/created_at/edit_count/files_affected per transaction; `transactions show TX_ID` emits header + edits + file_rename via models.serialize.to_dict; `transactions cancel TX_ID` deletes PENDING transactions only (clear error otherwise); `rollback TX_ID` mirrors `apply`. All JSON via json.dumps(indent=2); errors as {"error": ...} with exit 1. These commands operate on stored transactions, so the --no-refresh index-freshness pattern intentionally does not apply.
- src/pypeeker/storage/transaction_store.py: docstring update only ("future rollback command" is no longer future).

Tests:
- tests/test_applier.py: new TestRollback class — byte-identical round trip with status transitions PENDING->APPLIED->ROLLED_BACK, length-changing edit offsets, re-index restores old symbol, --include-file rename reversal, refusals (PENDING, already ROLLED_BACK, FAILED, not found, span modified post-apply, file modified outside spans), and re-apply-after-rollback refused.
- tests/test_transactions_cli.py (new): CliRunner coverage of help/lifecycle docs, list (empty, shape, status transitions across apply/rollback), show (+ not found), cancel (pending deletes, applied refused, not found), rollback (round trip, refuses pending/modified/not found).
- Full suite: 595 passed, 10 skipped, 0 failures.

Risks/notes: rollback is strict by design — any post-apply modification to an affected file (even outside edited spans) refuses rollback; users must revert manually in that case.
<!-- SECTION:FINAL_SUMMARY:END -->
