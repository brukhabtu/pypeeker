---
id: TASK-130
title: 'Bounded fixpoint for check --fix (--fix-until-clean), overlay-based'
status: Done
assignee: []
created_date: '2026-07-31 22:25'
updated_date: '2026-08-01 03:39'
labels: []
dependencies:
  - TASK-129
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan B in roadmap-plans.md (normative — with the sequencing adjustments applied: re-based onto the overlay substrate from Plan A rather than the mirror, SIMULATION_UNSAFE_RULES re-scoped as a write-safety guard, and the cross-iteration read-through test added). Opt-in --fix-until-clean flag: re-run rules over simulated post-fix state, plan newly revealed remedies until quiescence or bound; default check --fix byte-identical; one combined transaction; stop_reason always reported.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 All Plan B acceptance criteria in roadmap-plans.md are met, including byte-identical default-path JSON, single-transaction rollback restoring pre-loop bytes, guaranteed termination with honest stop_reason, and the flagged report being a strict superset of the frozen shape.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Execute roadmap-plans.md Plan B via its workflow, WITH the ITEM B sequencing adjustments applied (they supersede the plan text): the loop runs on a persistent simulation overlay (Plan A landed — no mirror, no apply_edits_to_mirror extraction, no materialize_mirror export); SIMULATION_UNSAFE_RULES re-scoped as a write-safety guard (born_private baseline writes land on the real tree under an overlay); obsolete mirror risks/tests deleted; the cross-iteration read-through test added (a remedy planned in iteration 2 must see iteration 1 bytes through the per-call overlay nesting). Opt-in --fix-until-clean; default check --fix byte-identical; one combined transaction; stop_reason always honest.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Stages 1-5 landed (implementation + gate green):
- refactor/batch.py: new public seam `apply_to_overlay(overlay, materialized, *, adapter=None, src_roots=None)` + `OverlayApplyError` (public face of the internal _ApplyRefused), both barrel-exported with the already-existing `flatten_store`. Module docstring re-scoped: the batch SCHEDULER stays single-pass; the check-fix fixpoint lives in app/check_fixes.py.
- check/simulation.py: `SIMULATION_UNSAFE_RULES = {"born-private"}`, documented as a WRITE-SAFETY guard (overlay project_root is the real root, so born_private baseline_path() writes would land in the user .pypeeker/). Exported through check/__init__. CheckEngine gained a read-only `config` property so app can derive the narrowed loop config.
- app/check_fixes.py: `_plan_pass` extracted (both paths call it); `apply_check_fixes(..., max_iterations=1)` branches to `_run_fixpoint` BEFORE any loop machinery exists, so the default path is structurally unreachable from it. Loop: one persistent OverlayIndexStore, per iteration re-run rules on the sim store (minus SIMULATION_UNSAFE_RULES), plan via submit_intent (per-call overlay nests over the loop overlay), splice the kept set as ONE batch via apply_to_overlay, re-bind. Guards: quiescent / repeated-fix (pre-apply) / cycle (SHA-256 over sorted (path, sha256(bytes)) of the whole simulated tree) / max-iterations. Exit: flatten_store with empty authorization sets -> ONE check-fix transaction, applied once; residual from the ORIGINAL engine on the REAL store. New `CheckFixSimulationError(code=simulation-failed|flatten-failed)` keeps refusals out of tracebacks.
- cli.py: `--fix-until-clean` + `--fix-max-iterations N` (default 10, min 2) with UsageErrors mirroring --plan; report gains iterations/iterations_run/quiescent/stop_reason ONLY when the outcome carries them, plus per-fix `iteration`.
- Docs: architecture.md wall 2 struck (scoped to the batch scheduler), Refactoring Model + Output contract folded in.
- Tests: tests/test_check_fix_until_clean.py ADDED (29 cases); no existing test file touched. Gate: pytest 1803 passed, ruff clean, index+check exit 0.

Review-fix pass (3 confirmed findings on the staged change):
- **External edit during the loop (finding 3, data loss).** `OverlayIndexStore` now records a per-path `base_preimages()` map — the SHA-256 the base held the first time the overlay read through or wrote each path. `flatten_store` gained opt-in `verify_preimages`, raising the new `StalePreimageError(FlattenError)` when a recorded path no longer matches; `_run_fixpoint` passes it and maps it to `CheckFixSimulationError(code="tree-changed")`. Previously the flatten re-read the anchor at flatten time, so `TransactionApplier._verify_hashes` passed against already-modified bytes and the whole-region splice destroyed the edit silently — strictly weaker than plain --fix, which fails closed. Opt-in so run_batch/privatize keep byte-identical behavior.
- **`fixes` non-empty with `tx_id: null` (finding 1).** The loop accumulated per-iteration repairs while the transaction is the NET diff, so an A->B->A run reported repairs with no transaction. `fixes` is now filtered at exit to repairs at least one of whose touched files still differs from the real tree; the rest go to a new flag-only `reverted` list. `fixes` non-empty <=> `tx_id` non-null is now exact in both directions.
- **Per-repair vs per-file attribution (finding 2).** Byte-level supersession was prototyped and rejected: the conflict-loser cascade produces the same overlap shape (iteration 1 deletes `os, ` at [7,11), iteration 2 deletes the whole line at [0,11)), so it would drop a repair the plan's acceptance criteria require in `fixes`. The guarantee is therefore per FILE, and the contract text says so (check_fixes/cli docstrings + architecture.md Output contract), with a test pinning the boundary.
- Report gains `reverted` and `iterations[].reverted`; both flag-only. Gate: pytest 1813 passed, ruff clean, index+check exit 0. No pre-existing test file edited.

Landed via its workflow (opus implementer). The loop: one persistent OverlayIndexStore per run; per iteration the engine runs on simulated state, remedies plan through submit_intent (whose per-call overlay nests over the loop overlay — the A-PR1 delegation fix carrying real load), kept repairs splice via the new public apply_to_overlay, re-bind, iterate. stop_reason always honest: quiescent / repeated-fix (checked BEFORE re-applying) / cycle (whole-tree state hashing) / max-iterations (default 10, min 2). Write-safety: SIMULATION_UNSAFE_RULES={born-private} (the only builtin that writes through project_root — audited), residual always computed by the original engine on the real store. One combined transaction through flatten_store; --plan writes it PENDING touching nothing.

Review: 10 findings, 3 must-fix — headline: an external-edit DATA-LOSS window (tree changed mid-loop would be silently overwritten at apply). Fixed with overlay base-preimage recording at first read + flatten verify_preimages -> tree-changed refusal. Default path frozen structurally (fixpoint unreachable without the flag, proven by monkeypatch test + golden report). Final gate 1813 pytest (+39, additions only), ruff clean, self-lint exit 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Bounded fixpoint for check --fix (Plan B / roadmap): opt-in --fix-until-clean.

What changed:
- app/check_fixes.py: _plan_pass extracted store-agnostic; _run_fixpoint iterates rule-run -> plan -> splice -> re-bind on one persistent overlay until quiescence or bound, with submit_intent nesting its per-call overlay over loop state; the default path is byte-identical and the loop structurally unreachable without the flag.
- Termination always honest: stop_reason in {quiescent, repeated-fix, cycle, max-iterations} — repeated fix_ids abandoned before re-applying, cycles detected by whole-tree state hashing.
- Write-safety: SIMULATION_UNSAFE_RULES excludes born-private from simulated re-runs (its baseline write lands on the real root even under an overlay); residual_violations always computed by the original engine, original config, real store, post-apply.
- One combined check-fix transaction; single rollback restores pre-loop bytes; --fix-until-clean --plan writes PENDING touching nothing; net-zero runs report tx_id null.
- Review-driven hardening: overlay base-preimage recording + flatten verify_preimages closes an external-edit data-loss window with a tree-changed refusal.
- CLI: --fix-until-clean + --fix-max-iterations with UsageError guards; flagged report is a strict superset (iterations breakdown, iterations_run, quiescent, stop_reason).

Tests: +39, additions only — cascade both directions, all four stop_reasons (incl. deliberate oscillators), rollback byte-parity, plan parity, cross-iteration read-through, fail-closed re-bind, write-safety snapshots, golden default report, structural unreachability. Gate: 1813 pytest, ruff clean, self-lint exit 0.
<!-- SECTION:FINAL_SUMMARY:END -->
