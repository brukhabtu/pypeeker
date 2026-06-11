---
id: TASK-64
title: >-
  Dead surfaces: implement or remove FileIndex.errors and TransactionStatus
  lifecycle
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 15:47'
updated_date: '2026-06-11 16:07'
labels:
  - models
  - refactor
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
FileIndex.errors is never populated (the binder never appends to state.errors; indexing errors go to IndexResult.errors), and TransactionStatus.APPLIED/FAILED/ROLLED_BACK can never occur because the applier deletes the transaction file on success and leaves PENDING on failure. Both invite readers to trust fields that are never written. Decide per surface: implement it for real or remove it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 FileIndex.errors is either populated by the binder (e.g. parse/bind recoverable errors) and surfaced, or removed from the model and serialization
- [x] #2 Transaction status transitions are real (status updated on apply/failure instead of file deletion, or enum trimmed to what actually happens); decision recorded in the task notes
- [x] #3 Serialization forward-compat handled for any removed/added fields; full test suite passes
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. FileIndex.errors: implement for real. In binder.bind(), after visit_module, if root.has_error, walk the tree (pruned by node.has_error) collecting ERROR and is_missing nodes; append concise entries like "syntax error at line N, column M" / "missing <type> at line N, column M" to state.errors. New tests in tests/test_binder_errors.py (new file to avoid concurrent-edit conflicts with test_binder.py): malformed source records errors while valid symbols still bind; clean source yields no errors.
2. TransactionStatus lifecycle: make it real (TASK-65 rollback needs applied transactions retrievable). TransactionStore gains update_status(tx_id, status) that rewrites the header line in place, keeping edit/rename lines. TransactionApplier.apply: on success set status=APPLIED (no longer remove the file); on mid-apply failure (after rollback) set status=FAILED then raise. Pre-flight failures (tx not found, non-pending, no edits, hash mismatch) leave PENDING since nothing was touched. Keep ROLLED_BACK in the enum for TASK-65. Applier keeps refusing non-PENDING transactions; result dict keys unchanged.
3. Update storage-transaction-architecture.md lifecycle section (surgical: replace "delete transaction file" steps with retained-with-status lifecycle).
4. Tests: update test_applier.py (transaction retained as APPLIED, re-apply refused, FAILED after rollback-on-error); add update_status tests to test_transaction_storage.py; serialization compat already covered by test_models.py round-trip of errors and header status defaults — add explicit forward-compat assertion if needed.
5. Run uv run pytest -q; verify scope-file tests pass.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Decisions (per AC#2):
- FileIndex.errors: IMPLEMENT for real. bind() now collects syntax errors from the tree-sitter CST (ERROR nodes and missing nodes, pruned walk via node.has_error) and records concise entries ("syntax error at line N, column M" / "missing X at line N, column M") so a partially-bound index is visibly partial.
- TransactionStatus: make the lifecycle REAL (TASK-65 rollback needs applied transactions retrievable). TransactionStore gains update_status(tx_id, status) rewriting only the header line. Applier marks APPLIED on success (file retained, no longer deleted) and FAILED after a mid-apply rollback. Pre-flight failures (tx not found, non-pending, no edits, hash mismatch) leave PENDING untouched. ROLLED_BACK kept in the enum for TASK-65 rollback command. Applier still refuses non-PENDING transactions; result dict keys unchanged.
- storage-transaction-architecture.md lifecycle section updated to the retained-with-status lifecycle (recorded decision).

Implementation complete:
- binder.bind() now records syntax errors via _collect_syntax_errors (pruned has_error walk; one entry per ERROR node, one per missing token, document order).
- TransactionStore.update_status(tx_id, status) rewrites only the header line; raises FileNotFoundError for unknown tx.
- Applier: success -> update_status(APPLIED) (transaction retained, no longer deleted); mid-apply failure after rollback -> update_status(FAILED); pre-flight failures leave PENDING. Result dict keys unchanged; non-PENDING still refused.
- storage-transaction-architecture.md lifecycle section updated (retained-with-status; rolled_back reserved for TASK-65).
- Tests: new tests/test_binder_errors.py (6 tests); applier tests for APPLIED retention, re-apply refusal, FAILED on mid-apply rollback, PENDING on pre-flight failure; update_status tests in test_transaction_storage.py; forward-compat tests in test_models.py.
- Full suite: 567 passed, 10 skipped.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Made the two dead surfaces real instead of removing them.

FileIndex.errors (now populated):
- `bind()` (src/pypeeker/binder/binder.py) collects syntax errors from the tree-sitter CST via a new `_collect_syntax_errors` helper: cheap `root.has_error` short-circuit, descent pruned to error-containing subtrees, one concise entry per ERROR node ("syntax error at line N, column M") and per missing token ("missing X at line N, column M"), in document order. A partially bound index is now visibly partial.

TransactionStatus lifecycle (now real; unblocks TASK-65 rollback):
- `TransactionStore.update_status(tx_id, status)` rewrites only the JSONL header line, preserving edit/rename lines; raises FileNotFoundError for unknown transactions.
- `TransactionApplier.apply`: on success the transaction is marked APPLIED and retained on disk (previously deleted); a mid-apply failure rolls files back and marks the transaction FAILED; pre-flight failures (not found, non-pending, no edits, hash mismatch) leave it PENDING. Only PENDING transactions can be applied (re-apply of APPLIED/FAILED is refused). ROLLED_BACK kept in the enum for the TASK-65 rollback command. Apply result dict keys unchanged for CLI compatibility.
- storage-transaction-architecture.md Transaction Log lifecycle updated to the retained-with-status model (recorded decision).

Serialization compat:
- No field shapes changed; added explicit forward-compat tests (FileIndex without "errors" defaults to [], TransactionHeader without "status" defaults to PENDING, unknown keys ignored).

Tests:
- New tests/test_binder_errors.py (malformed source records errors while valid symbols still bind; clean source clean; round-trip).
- test_applier.py: APPLIED retention, re-apply refusal, FAILED after mid-apply rollback, PENDING after pre-flight failure.
- test_transaction_storage.py: update_status behavior incl. header-line-only rewrite and missing-tx error.
- Full suite: 567 passed, 10 skipped.
<!-- SECTION:FINAL_SUMMARY:END -->
