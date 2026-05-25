---
id: TASK-51
title: 'Refactor foundation: INSERT/DELETE transaction edit operations'
status: Done
assignee: []
created_date: '2026-05-25 13:01'
updated_date: '2026-05-25 17:11'
labels:
  - refactor
  - foundation
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Generalize the transaction edit model beyond REPLACE so complex refactorings can express structural changes. Add INSERT (at a byte offset) and DELETE (a byte range) ops to EditEntry/EditOp; the applier applies and rolls them back, applying edits in descending byte order so offsets stay valid; hash-based conflict detection and atomic apply/rollback continue to work. Rename (REPLACE) is unchanged.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 EditOp gains INSERT and DELETE; EditEntry supports them (INSERT has an insertion point + text; DELETE has a byte range)
- [x] #2 TransactionApplier applies INSERT/DELETE correctly (edits applied in descending offset order within a file) and rolls them back on failure
- [x] #3 Existing REPLACE/rename behavior and conflict detection are unchanged; tests cover insert, delete, and mixed edits round-tripping
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
EditOp gains INSERT and DELETE. The applier already applies edits as descending-offset byte splices (content[start:end]=new), so INSERT (start==end, old="") and DELETE (new="") work through the same mechanism with no applier change; documented the encoding on EditOp. Conflict detection (file_hash) and rollback unchanged. Tests: insert, delete, mixed insert+delete+replace (non-overlapping offsets) round-trip correctly.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Generalized transaction edits beyond REPLACE: added INSERT and DELETE ops. All text edits share one byte-splice mechanism (content[start:end]=new), so INSERT is a zero-width edit with empty old and DELETE is a range with empty new - the applier needed no change, only the op vocabulary and documented semantics. Foundation for structural refactors (extract/move). 465 tests pass; pypeeker check exits 0.
<!-- SECTION:FINAL_SUMMARY:END -->
