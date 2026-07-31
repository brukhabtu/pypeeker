---
id: TASK-123
title: 'refactor: everything-is-a-batch (single execution path)'
status: Done
assignee:
  - '@claude'
created_date: '2026-07-31 04:51'
updated_date: '2026-07-31 15:46'
labels:
  - refactor
  - architecture
dependencies:
  - TASK-122
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 3: single-op CLI commands (plan-rename, plan-extract-*, plan-inline) route through the batch engine as a batch of one; the direct-planner call path is removed from cli/app. Output contracts preserved.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 All mutating CLI entry points build intents and go through schedule->materialize->transaction; direct planner invocation from cli/app is gone.
- [x] #2 JSON output shapes and exit codes unchanged (frozen contract); full gate green.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. app/ gains a submit path: build Intent(s) -> schedule (always, even for one) -> materialize. Batch-of-one materializes against the real store so the planner saves its own transaction and returns its own TransactionSummary — preserving the frozen JSON contract byte-for-byte; multi-intent keeps the mirror+flatten path.
2. cli.py single-op commands (plan-rename, plan-inline, plan-extract-*) construct intents and call the app submit path; direct planner construction leaves cli.
3. promote/demote via a new visibility intent kind + registered materializer if it wraps cleanly; otherwise principled deferral note.
4. Materializer error strings surface as the same per-command error codes/exit codes as today.
5. Existing CLI tests as the contract oracle; new tests for the submit path incl. conflict/cycle validation on singles.
6. Gate + 3-lens opus adversarial review; orchestrator verifies, commits, PRs, merges.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented via the phase3-everything-is-a-batch workflow (sonnet implementer, haiku gates, 3 opus lenses). app/submit.py: submit_intent always schedules (conflict/cycle validation even for one intent), then materializes through the phase-2 registry directly against the real store — the planner persists its own transaction and its exact TransactionSummary rides back on Materialized.summary, so output is byte-identical by construction, not reconstruction. MaterializeError(str) carries .code so demote/promote surface their seven distinct refusal codes unchanged. promote/demote converted via new ChangeVisibilityIntent.

Adversarial review: 7 findings, 1 must-fix — the architecture lens caught a footprint UNDER-approximation: promote --add-export writes the package __init__.py, invisible to find_importers, so the declared footprint was a strict subset of real writes. Fixer unioned the export-target init file into footprint reads/writes + effect. Orchestrator re-verified: 1473 pytest (1460 pre-existing unmodified + 13 new), ruff clean, self-lint exit 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Everything is a batch: single-op CLI commands route through the batch engine (phase 3).

What changed:
- New app/submit.py: submit_intent always runs the scheduler (real conflict/cycle validation for singles), then materializes via the phase-2 planner registry against the real store. Materialized gains optional summary/warnings so each planner exact TransactionSummary rides back — the frozen CLI JSON contract is preserved by construction. submit_intents dispatches singles there and delegates multi-intent batches to run_batch unchanged.
- plan-rename, plan-inline-variable, plan-extract-variable, plan-extract-method, demote, promote now build an Intent and call submit_intent; no planner class or error type is imported in those command bodies.
- promote/demote wrapped via new ChangeVisibilityIntent with a conservative footprint (stand-in RenameIntent, plus the export-target __init__.py after review); registered change-visibility materializer beside VisibilityPlanner.
- MaterializeError preserves per-command refusal codes (already-private, protected-public-api, rename-refused, not-found, already-public, dunder, export-target) exactly.

Contract proof: zero existing tests modified (they are the oracle); 13 new tests prove summary parity field-by-field vs direct planner calls, refusal parity incl. codes, scheduler-runs-for-singles, multi-path conflict, and ChangeVisibilityIntent dispatch. 1473 pytest, ruff clean, self-lint exit 0.
<!-- SECTION:FINAL_SUMMARY:END -->
