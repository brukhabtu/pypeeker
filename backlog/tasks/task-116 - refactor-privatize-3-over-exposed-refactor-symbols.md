---
id: TASK-116
title: 'refactor: privatize 3 over-exposed refactor symbols'
status: Done
assignee:
  - '@claude'
created_date: '2026-07-30 23:13'
updated_date: '2026-07-30 23:16'
labels:
  - refactor
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
review/06 decision 4: Schedule, schedule (batch.py) and ConflictReport (footprint.py) are module-__all__ public but reached by no test, no barrel, and no other module. Privatize them to _-prefixed names and drop from __all__. The other decision-4 categories (rule functions, run_batch result types, test-only seams) stay public. The freeze-contracts half of the review (symbol-ID grammar + CLI JSON envelope) is already documented, so this closes out the review cleanup.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Full gate green: pytest, ruff, and pypeeker check (incl. unused-public-symbol and barrel-only) all pass.
- [x] #2 Schedule (class) and ConflictReport are renamed to underscore-prefixed names and removed from their module __all__, with all intra-module references and docstring xrefs updated. The schedule function is kept public: it is a real test seam used 18 times by test_batch.py, so decision 4's no-test-touches-them premise did not hold for it.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Verify external importers of Schedule/schedule/ConflictReport.
2. Rename to underscore-prefixed names and drop from __all__.
3. Gate caught test_batch.py imports schedule (earlier grep hid it via a batch.py substring match); revert schedule to public per decision 4 test-seam guidance, keep the two types private.
4. Gate.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Privatized Schedule (batch.py) and ConflictReport (footprint.py): both had zero references from src, tests, and the package barrel. schedule stays public - it is imported and called 18 times by tests/test_batch.py; decision 4 mislabeled it because the review grep excluded test_batch.py via a batch.py substring match. A public schedule() returning the now-private _Schedule is fine: callers use the instance, never the type name.

The freeze-contracts half of the review cleanup (symbol-ID grammar + CLI JSON envelope declared frozen/additive-only) was already in architecture.md and storage-transaction-architecture.md, matching the shipped flat _emit_error shape, so no change was needed.

Gate: 1398 pytest passed, ruff clean, pypeeker check exit 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Privatize two genuinely over-exposed refactor types, closing out review/06 decision 4.

What changed:
- refactor/batch.py: Schedule -> _Schedule (class), dropped from __all__.
- refactor/footprint.py: ConflictReport -> _ConflictReport, dropped from __all__.
- Intra-module references and the batch.py module-docstring xref updated to match.

Left public intentionally:
The schedule function is imported and called 18 times by tests/test_batch.py, making it a real test seam. Decision 4 mislabeled it over-exposed-only because the review grep excluded test_batch.py via a batch.py substring match. Per the review own test-seam guidance it stays public; it returns the now-private _Schedule, which callers use without naming.

Freeze-contracts note:
The other half of the review cleanup - declaring the symbol-ID grammar and CLI JSON envelope frozen/additive-only - was already documented and matches the shipped flat _emit_error shape, so no doc change was needed.

Tests: 1398 pytest passed, ruff clean, pypeeker check (incl. unused-public-symbol, barrel-only) exit 0.
<!-- SECTION:FINAL_SUMMARY:END -->
