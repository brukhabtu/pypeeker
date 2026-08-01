---
id: TASK-129
title: 'One execution path: store-read port, mirror to overlay, fast-path collapse'
status: In Progress
assignee: []
created_date: '2026-07-31 22:25'
updated_date: '2026-08-01 00:10'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan A in roadmap-plans.md (normative, incl. sequencing adjustments — 19 disk-coupled sites not 14, and the nested-overlay read_file/file_exists delegation bug fix folded into PR1). Three sequential PR slices, each its own workflow execution: PR1 store-read port (read_file/file_exists/file_hash on the store surface, all planner reads routed through it); PR2 swap the temp-dir mirror for OverlayIndexStore (materialize_mirror deleted, flatten reads overlay state); PR3 collapse submit_intent single-intent fast path onto run_batch while every CLI contract stays byte-identical.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Each PR slice lands independently green (pytest/ruff/self-lint); all Plan A acceptance criteria in roadmap-plans.md are met, including zero test-file edits in PR1, no len==1 branch in app/submit.py after PR3, and every refusal code reachable before the collapse reachable after it.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Execute roadmap-plans.md Plan A slice by slice, each its own workflow with orchestrator verify + PR + merge between. PR1 (now): the store-read surface (read_file/file_exists/file_hash) + port all 19 disk-coupled sites across refactor/ and analysis/ (applier.py excluded by contract) + fix the nested-overlay read_file/file_exists delegation bug + replace the getattr duck-typing precedents with the real API. Zero test-file edits — the whole suite is the proof. PR2: mirror -> OverlayIndexStore. PR3: fast-path collapse.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
PR1 landed via its workflow: read_file/file_exists/file_hash on IndexStore with proper OverlayIndexStore overrides incl. the nested-overlay delegation bug fix (read_file/file_exists now delegate to base-store API — proven by 5 composition tests); all 19 disk-coupled sites ported per the corrected inventory with per-site missing-file behavior preserved; getattr duck-typing precedents (hierarchy.py, materialize_mirror) became the real API. Disciplined scope: privatize.py mirror rewrite and batch.py mirror internals correctly left for PR2 per the normative spec.

Review: 12 findings, 0 must-fix (advisories: batch.py docstring staleness that PR2 rewrites, the accepted CRLF read_text deviation, is_file narrowing — all pinned by tests). Gate: 1683 pytest (1648 pre-existing UNMODIFIED + 35 new), ruff clean, self-lint exit 0. Zero pre-existing test edits — the suite was the proof.
<!-- SECTION:NOTES:END -->
