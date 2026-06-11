---
id: TASK-97
title: 'fix: mass-demotion plans via the batch planner'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 18:28'
updated_date: '2026-06-11 22:17'
labels:
  - fix
  - visibility
  - m5-visibility
dependencies:
  - TASK-89
  - TASK-96
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Apply the visibility principle to a whole codebase: over-exposed findings become demotion intents scheduled by the composite planner (id-changing ops, collision handling, guarded re-validation). Acceptance: a demotion plan over pypeeker's own src that the suite survives.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Visibility findings convert to demote intents; plan-batch schedules and flattens them with drops reported
- [x] #2 Collisions (existing _name) and hierarchy/public-root refusals drop cleanly with reasons
- [x] #3 Dogfood: batch demotion plan over pypeeker applied on a scratch branch with the full suite passing; results recorded in notes
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Embed full symbol_id in messages of the three demotion-feeding rules (over-exposed-module-symbol, unused-public-symbol, test-only-production-code); add demote_entry(violation) extraction helper following rename_pair precedent
2. Add pypeeker privatize CLI command (--rule/--apply/--no-refresh/--include-heuristic) composing check -> demote_entry -> plan_privatize, JSON outcome report, exit 1 when nothing plannable
3. New tests/test_privatize_cli.py: end-to-end fixture package (plan-only, --apply, skips, rule selection, exit codes) + per-rule demote_entry unit tests; update affected message-format tests minimally
4. Dogfood (AC#3): scratch git branch, run privatize over pypeeker src, apply, full suite; record narrative in notes; merge back only if clean
5. Quality gates: pytest, ruff, self index+check green
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Embedded full symbol ids in the messages of over-exposed-module-symbol, unused-public-symbol and test-only-production-code; added check/demotion.py with demote_entry(violation) -> (symbol_id, confidence_str) and DEMOTION_RULES (rename_pair precedent)
- Added `pypeeker privatize [--rule ...] [--apply] [--include-heuristic] [--no-refresh]`: runs the selected demotion-feeding rules with pyproject options + visibility config injected, extracts entries, plans via plan_privatize (ONE flattened transaction), prints {tx_id, executed, dropped, skipped, warnings, files_affected, edit_count}; exit 1 when nothing plannable; --apply runs TransactionApplier
- New tests/test_privatize_cli.py (16 tests): plan-only tree-untouched + tx inspectable, --apply incl. defining-module __all__ rewrite, heuristic gate, rule selection, nothing-plannable exit 1, per-rule demote_entry extraction. Note: barrel-exported symbols can never reach the CLI (all three rules exempt barrels), so barrel rewrites stay covered at planner level in test_privatize.py
- Updated message-format assertions minimally across 6 existing test files; full suite 1346 passed, ruff clean

DOGFOOD NARRATIVE (AC#3), executed on local branch scratch/privatize-dogfood (left in place; no commits per session ground rules — the working tree carries the result back to the working branch):

1. Rule selection (deliberate): over-exposed-module-symbol ONLY. test-only-production-code cannot fire here — pypeeker indexes src only, so no test references exist in the index; symbols consumed only by tests surface as zero-reference over-exposed findings instead. unused-public-symbol is a strict subset of over-exposed on this codebase (zero refs anywhere vs zero refs outside module) and would only add pending-collision duplicate skips.
2. First plan: 72 demotions — rejected and cancelled: it would rename click command functions (cli:check -> _check), changing the user-facing CLI command names (the documented decorator-registered entry-point false-positive class). Cure: added [tool.pypeeker.visibility] allow-decorators = ["main.command", "main.group", "transactions.command"] to pyproject.
3. Second plan: tx 23cb4b83daec — 51 demotions across 31 files, 0 dropped, 24 skipped (all heuristic-confidence: symbols in modules touching getattr/globals — left alone on purpose; that gate exists for a reason). Applied.
4. Suite after apply: 26 collection errors — tests import production names that were honestly demoted (rule functions, rename_pair, to_snake_case, Capability, shadow_suffix, privatize dataclasses, ...). Adjusted TEST IMPORTS ONLY, mechanically: `from m import _name as name` aliases in 25 test files (production code untouched).
5. Remaining 5 failures exposed a REAL BUG: ExtractVariableError was falsely flagged — the binder does not record name references inside parenthesized `except (A, B)` tuples (batch.py:612, cli.py:582), while bare `except A` is captured (which is why ExtractMethodError was correctly spared). The rename rewrote the definition + imports but not the invisible except-tuple use sites -> NameError. Handled honestly: reverted that single demotion (4 sites), pinned it via [tool.pypeeker.over-exposed-module-symbol] allow with an explanatory comment, and filed TASK-102 for the binder fix.
6. Net dogfood result: 50 symbols demoted to _name across 31 src files; final `uv run pytest -q` = 1346 passed, ruff clean, self-lint (pypeeker index src && pypeeker check) exit 0. privatize re-run is a fixed point: nothing plannable (exit 1), only the 24 heuristic skips remain.
7. MERGE-BACK DECISION: YES — brought back to claude/architecture-review-gaps-twdi27 (suite/ruff/self-lint green there too). The demotions document real scope, the CLI surface is preserved, and the project is app-mode so no published Python API regressed. The applied transaction 23cb4b83daec remains in .semantic-tool/transactions/ as the rollback anchor.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Mass-demotion plans via the batch planner: check findings now drive `pypeeker privatize`, and the feature was dogfooded on pypeeker itself (50 symbols demoted, suite green).

Changes:
- The three demotion-feeding rules (over-exposed-module-symbol, unused-public-symbol, test-only-production-code) now embed the full symbol id in their messages; new `check/demotion.py` provides `demote_entry(violation) -> (symbol_id, confidence_str)` and `DEMOTION_RULES`, following the TASK-91 rename_pair handoff idiom (refactor never imports check).
- New CLI `pypeeker privatize [--rule NAME ...] [--apply] [--include-heuristic] [--no-refresh]`: runs the selected rules with pyproject options (visibility config injected even when the rules are not in the enabled set), extracts entries, plans ONE flattened transaction via plan_privatize, prints {tx_id, executed, dropped, skipped, warnings, files_affected, edit_count}; plan-only stops at the persisted PENDING transaction, --apply executes via TransactionApplier; exit 1 when nothing was plannable.
- Self-lint hardening: privatize.py CandidateEntry switched from a PEP 695 `type` alias to a plain assignment (the binder does not bind `type` statements), and the CLI avoids a walrus-in-comprehension the binder cannot see.
- Dogfood applied and merged back: 50 demotions across 31 src files; pyproject gained [tool.pypeeker.visibility] allow-decorators for click entry points and a documented allow for one binder false positive; test imports updated via `_name as name` aliases in 25 files. The dogfood exposed a real binder gap (references inside parenthesized except tuples are not indexed) — filed as TASK-102 after reverting that one demotion.

Tests:
- New tests/test_privatize_cli.py (16 tests): plan-only leaves the tree untouched with an inspectable transaction, --apply lands renames incl. defining-module __all__ rewrite, heuristic gate, rule selection, nothing-plannable exit code, per-rule demote_entry extraction. (Barrel rewrites stay covered at planner level: all three rules exempt barrel-exported symbols, so they cannot reach the CLI.)
- Full suite 1346 passed; ruff clean; `pypeeker index src && pypeeker check` exit 0.

Risks/follow-ups:
- TASK-102: binder misses except-tuple references (workaround pinned in pyproject).
- scratch/privatize-dogfood remains as a local branch ref; the applied transaction 23cb4b83daec in .semantic-tool/transactions/ is the rollback anchor.
<!-- SECTION:FINAL_SUMMARY:END -->
