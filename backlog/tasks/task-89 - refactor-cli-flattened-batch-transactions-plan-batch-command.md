---
id: TASK-89
title: 'refactor/cli: flattened batch transactions + plan-batch command'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 18:27'
updated_date: '2026-06-11 21:31'
labels:
  - refactor
  - cli
  - m3-planner
dependencies:
  - TASK-88
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
After simulation, diff overlay content against original disk per file and emit ONE ordinary transaction (hash-anchored to plan-time files, old=original content for rollback) so apply/rollback reuse the existing applier unchanged. CLI: plan-batch consumes a set of intents (from fix-emitting rules and/or an intent file), prints the plan summary incl. dropped intents; apply/rollback work as today.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Simulation output flattens to a single transaction applying atomically via the existing applier, with rollback restoring originals byte-identically
- [x] #2 plan-batch CLI plans multi-intent batches (e.g. all check --fix fixes plus explicit renames) and reports executed/dropped intents with reasons
- [x] #3 End-to-end test: a batch mixing a rename, an inline, and a delete that would corrupt under naive sequential transactions lands correctly
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. batch.py: add FlattenError + flatten_batch(result, real_store) -> (TransactionHeader, list[EditEntry]) — walk the mirror tree (excluding .semantic-tool), diff final mirror bytes vs original real-tree bytes, error on created/deleted/renamed files, emit one line-trimmed whole-file REPLACE/INSERT/DELETE EditEntry per changed file hash-anchored to the real file (file_hash = sha256 of real bytes, old==content[start:end] guaranteed by line-boundary trim).
2. cli.py: intent-file parser (JSON list of {kind, params, id?, deps?}; kind "fix" {rule} expands to FixIntents from DECLARED-confidence fix-carrying violations of that rule, dep references resolved through the expansion) + plan-batch command: refresh, build intents, run_batch in a temp mirror, flatten, persist via TransactionStore (operation "batch"), JSON {tx_id, executed, dropped, files_affected, edit_count}; exit 1 on all-dropped/abort/malformed input.
3. Tests: extend tests/test_batch.py (flatten correctness, text-guard, apply+rollback byte-identical, created/deleted/rename errors); new tests/test_plan_batch_cli.py (e2e rename+inline+fix sweep, naive-sequential corruption contrast, dropped reporting, abort policy exit 1, malformed file).
4. Quality gates: uv run pytest -q, ruff check, pypeeker index src && pypeeker check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Added FlattenError + flatten_batch(result, real_store) to refactor/batch.py: walks the mirror (excluding .semantic-tool), diffs final mirror bytes vs original real-tree bytes, errors on created/deleted/renamed files (v1 limitation, documented on FlattenError), and emits one line-trimmed splice EditEntry per changed file hash-anchored to the real plan-time file (old == content[start:end] guaranteed because trimming happens at line boundaries, which are also UTF-8-safe cut points). Header operation "batch", rename-shaped fields empty per the check-fix convention.
- Edit granularity: whole-file replace shrunk by a common leading/trailing LINE trim (not a real line diff) — verifiably correct and applier-compatible; INSERT/DELETE/REPLACE op chosen from the trimmed old/new emptiness.
- cli.py: plan-batch INTENTS_FILE [--policy skip|abort] [--no-refresh]; _build_batch_intents parses the JSON intent list (rename/inline-variable/extract-variable/extract-method/fix), fix entries expand via _expand_fix_rule (check engine run with only that rule; DECLARED-confidence fix-carrying violations -> FixIntents named {id}-{n}; eligibility filter deliberately mirrors _apply_check_fixes, light duplication noted in its docstring); deps naming a fix entry resolve through the expansion. Mirror temp dir cleaned in finally. apply/rollback verified unchanged.
- Tests: 7 flatten tests in test_batch.py (text-guard + hash anchoring, apply+rollback byte-identical round trip, line trim, net-noop, created/deleted/rename errors) and 12 CLI tests in new tests/test_plan_batch_cli.py (e2e rename+inline+fix-sweep apply, rollback, naive-sequential-staleness vs batch contrast for AC3, dropped reporting, abort exit 1, all-dropped exit 1, malformed inputs).
- Gates: uv run pytest -q (1198 passed), ruff clean, pypeeker index src && pypeeker check exit 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Flattened batch transactions + plan-batch command (TASK-89).

A simulated batch (TASK-88 run_batch) now flattens into ONE ordinary transaction the existing applier executes and reverts unchanged, and a new plan-batch CLI command drives the whole pipeline from a JSON intents file.

Changes:
- refactor/batch.py: flatten_batch(result, real_store) diffs every file the batch touched (mirror walk) against the original real tree and emits one hash-anchored EditEntry per changed file: file_hash = real file's SHA-256, old = original text, so apply is atomic+verified and rollback restores originals byte-identically. Entries are whole-file replaces shrunk by a common leading/trailing line trim (line boundaries keep the applier's old==content[start:end] guard exact and the cut points UTF-8-safe). FlattenError on mirror-created, mirror-deleted, or intent-renamed files — v1 transactions cannot express those (documented).
- cli.py: `pypeeker plan-batch INTENTS_FILE [--policy skip|abort] [--no-refresh]` — refreshes the index, parses intents ({kind: rename|inline-variable|extract-variable|extract-method|fix, kind params mirroring the plan-* commands, optional id/deps}), runs the batch against a temp mirror, flattens, persists via TransactionStore (operation "batch"), prints {tx_id, executed, dropped, files_affected, edit_count}. Exit 1 with {"error": ...} on malformed input, policy=abort aborts, or all intents dropped. kind "fix" {rule} runs check with only that rule and wraps each DECLARED-confidence fix-carrying violation as a FixIntent ({id}-{n}); deps naming the sweep entry resolve to its expansion. The fix eligibility filter intentionally mirrors check --fix (light duplication, noted).
- apply/rollback untouched and verified to work on flattened transactions.

Tests:
- tests/test_batch.py: flatten correctness (text guard, real-file hash anchoring, splice == mirror prediction), apply+rollback byte-identical round trip, line trimming, net-noop, created/deleted/renamed errors.
- tests/test_plan_batch_cli.py (new): e2e rename + inline + unused-imports fix sweep -> plan-batch -> apply with final contents equal to the mirror prediction; the AC3 contrast (pre-planned sequential rename+inline transactions go stale at the applier's hash guard where plan-batch lands the same pair); dropped-intent reporting; --policy abort exit 1; all-dropped exit 1; malformed intents files.

Results: uv run pytest -q 1198 passed; ruff clean; pypeeker index src && pypeeker check exit 0.

Risks/limitations: v1 flattening refuses file renames/creations/deletions (FlattenError); --include-file renames must go through plan-rename individually.
<!-- SECTION:FINAL_SUMMARY:END -->
