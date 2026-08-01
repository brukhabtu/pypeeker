---
id: TASK-129
title: 'One execution path: store-read port, mirror to overlay, fast-path collapse'
status: Done
assignee: []
created_date: '2026-07-31 22:25'
updated_date: '2026-08-01 01:45'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan A in roadmap-plans.md (normative, incl. sequencing adjustments — 19 disk-coupled sites not 14, and the nested-overlay read_file/file_exists delegation bug fix folded into PR1). Three sequential PR slices, each its own workflow execution: PR1 store-read port (read_file/file_exists/file_hash on the store surface, all planner reads routed through it); PR2 swap the temp-dir mirror for OverlayIndexStore (materialize_mirror deleted, flatten reads overlay state); PR3 collapse submit_intent single-intent fast path onto run_batch while every CLI contract stays byte-identical.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Each PR slice lands independently green (pytest/ruff/self-lint); all Plan A acceptance criteria in roadmap-plans.md are met, including zero test-file edits in PR1, no len==1 branch in app/submit.py after PR3, and every refusal code reachable before the collapse reachable after it.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Execute roadmap-plans.md Plan A slice by slice, each its own workflow with orchestrator verify + PR + merge between. PR1 (now): the store-read surface (read_file/file_exists/file_hash) + port all 19 disk-coupled sites across refactor/ and analysis/ (applier.py excluded by contract) + fix the nested-overlay read_file/file_exists delegation bug + replace the getattr duck-typing precedents with the real API. Zero test-file edits — the whole suite is the proof. PR2: mirror -> OverlayIndexStore. PR3: fast-path collapse.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
PR1 landed via its workflow: read_file/file_exists/file_hash on IndexStore with proper OverlayIndexStore overrides incl. the nested-overlay delegation bug fix (read_file/file_exists now delegate to base-store API — proven by 5 composition tests); all 19 disk-coupled sites ported per the corrected inventory with per-site missing-file behavior preserved; getattr duck-typing precedents (hierarchy.py, materialize_mirror) became the real API. Disciplined scope: privatize.py mirror rewrite and batch.py mirror internals correctly left for PR2 per the normative spec.

Review: 12 findings, 0 must-fix (advisories: batch.py docstring staleness that PR2 rewrites, the accepted CRLF read_text deviation, is_file narrowing — all pinned by tests). Gate: 1683 pytest (1648 pre-existing UNMODIFIED + 35 new), ruff clean, self-lint exit 0. Zero pre-existing test edits — the suite was the proof.

PR2 landed via its workflow (opus implementer): materialize_mirror and BatchResult.root DELETED; run_batch simulates on OverlayIndexStore with a required keyword-only tx_store (never derived from project_root); _apply_to_overlay preserves the two-phase splice discipline with renames as write+tombstone; flatten factored as the reusable flatten_store(sim_store, real_store, operation, authorized_created/deleted) seam per the sequencing adjustment — empty sets now, D-PR2 extends it. privatize ported to the overlay end-to-end. Snapshot-invariance proofs: whole-directory byte maps (incl. .pypeeker/) identical across batch/drop/abort; base index cache verified unpolluted BY IDENTITY.

Review: 8 findings, 2 must-fix — both real mirror-vs-overlay divergences at the index edges (drop vocabulary for on-disk-but-unindexed files; vanished-source filtering the mirror copy step used to provide). Every pre-existing test edit traces to a deleted API (work_dir/BatchResult.root/materialize_mirror), with TestMaterializeMirror replaced one-for-one plus a fourth assertion the mirror could not express. Final gate 1702 pytest, ruff clean, self-lint exit 0.

PR3 landed via its workflow (opus implementer): submit_intent is now literally run_batch([intent], ALL_OR_NOTHING) with the caller tx_store passed through verbatim; planner outcomes ride out on the additive ExecutedIntent.summary/.warnings and DroppedIntent.code fields; check_fixes needed ZERO changes (fresh overlay per call preserves its plan-against-pre-fix-state property, proven end-to-end). Implementer mutation-tested its own wiring (deleting the code-carry getattr fails 18 tests) and verified the structural proofs fail against pre-collapse code. Exactly one pre-existing test touched — a rename of a test whose name asserted the deleted concept, assertions frozen character-for-character.

Review: 12 findings, 1 must-fix (per-submission splice+parse+bind cost — addressed by fixer). 37 new proof tests: structural no-fast-path (registry spy proving the engine loop ran), field-for-field parity, a 13-case refusal-code reachability matrix through the CLI, no-stray-transaction snapshots, check --fix interaction. Final gate 1744 pytest, ruff clean, self-lint exit 0. ALL THREE SLICES OF PLAN A ARE DONE: one execution path, walls-list bullet deleted, everything-is-a-batch caveat removed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
One execution path (Plan A / roadmap, three PRs): store-read port, mirror-to-overlay swap, fast-path collapse.

PR1 (#91): IndexStore gains read_file/file_exists/file_hash with overlay-aware overrides incl. the nested-overlay delegation bug fix; all 19 disk-coupled planner sites ported with zero test edits — the whole suite was the proof.
PR2 (#92): materialize_mirror and BatchResult.root deleted; run_batch simulates on OverlayIndexStore with a required explicit tx_store; flatten factored as the flatten_store seam with authorization parameters for file birth/death to extend; simulation isolation proven by whole-directory byte snapshots and identity-checked cache purity; 2 must-fix mirror-vs-overlay divergences at the index edges caught and restored.
PR3 (this): submit_intent = run_batch([intent], ALL_OR_NOTHING); planner summaries and refusal codes carried out via additive engine fields; every CLI contract byte-identical (frozen oracles unmodified); check --fix untouched; 13-code refusal reachability matrix and no-stray-transaction snapshots pinned.

Net: two execution substrates became one; batch-of-one costs an overlay, not a directory copy; the architecture walls list lost its third bullet. Final: 1744 pytest, ruff clean, self-lint exit 0.
<!-- SECTION:FINAL_SUMMARY:END -->
