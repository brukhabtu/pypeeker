---
id: TASK-91
title: 'fix: naming-convention rule + cross-module convention renames'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 18:27'
updated_date: '2026-06-11 21:46'
labels:
  - fix
  - m4-program-fixes
dependencies:
  - TASK-84
  - TASK-89
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
snake_case functions, PascalCase classes, UPPER_CASE module constants — detectable from symbol kind+name; fixable only by a barrel-aware, confidence-gated, whole-program rename. Batch renames ride the composite planner (id-changing intents, collision handling).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Rule flags convention violations per kind with configurable conventions/allowlist
- [x] #2 Fixes plan cross-module renames (exports per policy flag) gated to declared/direct resolution; collisions drop with reasons
- [x] #3 End-to-end batch test renaming multiple symbols incl. two that would collide naively
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. New file-scoped opt-in rule check/builtin/naming_conventions.py: snake_case for function/method (+ variable/parameter/property via kinds option), PascalCase for class; dunders skipped, leading underscores stripped before checking and preserved in suggestions; conventions (per-kind regex override) and allow (fnmatch) options; message embeds symbol_id + suggested name; public to_snake_case/to_pascal_case converters + rename_pair() extractor for the violations->pairs handoff.
2. New refactor/convention_renames.py (no check import — callers extract (symbol_id, suggested_name) pairs): convention_rename_intents(store, pairs, include_exports=False) -> (RenameIntent list with deterministic ids, skipped list with reasons: no-op, symbol-not-found, duplicate-symbol, target-exists in same scope, pending-collision with earlier intent, override-unsafe via analysis.Hierarchy pre-check); write_intents_file() emits plan-batch-compatible JSON.
3. tests/test_rule_naming_conventions.py: per-kind detection, HTTPServer-style suggestions, underscore/dunder tolerance, kinds/conventions/allow options, opt-in.
4. tests/test_convention_renames.py: converter unit tests (skip reasons, pending-collision determinism), intents-file schema, and the AC end-to-end: tmp project with def BadName / class bad_class, cross-module call sites + barrel re-export -> rule -> pairs -> intents -> run_batch -> flatten_batch -> TransactionApplier.apply -> all call sites and barrel updated; colliding pair drops exactly one with reason.
5. uv run pytest -q + ruff clean.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Implemented check/builtin/naming_conventions.py (file-scoped opt-in rule, snake/Pascal converters, rename_pair extractor) and refactor/convention_renames.py (pairs -> RenameIntent converter with 6 skip reasons + plan-batch intents-file writer). Writing tests next.

- Tests written: tests/test_rule_naming_conventions.py (34) and tests/test_convention_renames.py (18, incl. the AC end-to-end with barrel re-export and the naively-colliding pair).
- Full suite: 1297 passed; ruff clean repo-wide.
- Skip-reason inventory in converter: no-op, symbol-not-found, duplicate-symbol, target-exists, pending-collision (first-submitted wins, deterministic), override-unsafe (Hierarchy pre-check; planner MethodOverrideSafe stays the batch-time backstop).
- Honest v1 scope: UPPER_SNAKE-for-module-constants NOT implemented (constants not statically distinguishable); default variable convention tolerates UPPER_SNAKE instead so opting into variable checks does not flag constants.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added the naming-conventions rule and the cross-module convention-rename pipeline (rule findings -> RenameIntents -> run_batch/plan-batch).

Changes:
- src/pypeeker/check/builtin/naming_conventions.py — new file-scoped, opt-in rule: snake_case for function/method (+ property/variable/parameter selectable via `kinds`; variable also tolerates UPPER_SNAKE since constants are not statically distinguishable in v1), PascalCase for class. Dunders skipped, leading underscores stripped before matching and preserved in suggestions. Options: `kinds`, `conventions` (per-kind regex override; suggester stays the kind default), `allow` (fnmatch on name/symbol_id/module). Each finding embeds the symbol id and a suggested conforming name; public converters to_snake_case (HTTPServer -> http_server, parseHTML2Text -> parse_html2_text, underscore runs collapse) / to_pascal_case, and rename_pair() extracts (symbol_id, suggested_name) from a finding — the check->refactor handoff, since refactor may not import check.
- src/pypeeker/refactor/convention_renames.py — convention_rename_intents(store, pairs, include_exports=False) converts pairs into RenameIntents with deterministic ids (convention-rename:<symbol_id>), skipping with machine-readable reasons: no-op, symbol-not-found, duplicate-symbol, target-exists (suggested name already bound in the same scope), pending-collision (two pairs claiming one name in one scope: later-submitted pair drops deterministically, pre-batch), override-unsafe (METHOD override edges / unknown MRO via analysis.Hierarchy — naming-flavoured triage; the planner\u2019s MethodOverrideSafe remains the batch-time backstop). Declared/direct reference gating is inherited from RenamePlanner by construction (include_receivers left off), documented rather than re-implemented. write_intents_file() emits plan-batch-compatible {"kind": "rename", ...} JSON, completing check -> converter -> plan-batch.

Tests:
- tests/test_rule_naming_conventions.py (34): per-kind detection + suggestions, acronym/digit edge cases, underscore/dunder tolerance, kinds/conventions/allow options, opt-in, rename_pair.
- tests/test_convention_renames.py (18): converter conversion + every skip reason (incl. submission-order determinism of pending-collision), intents-file schema, and the AC end-to-end: findings -> intents -> run_batch -> flatten_batch -> TransactionApplier over a tmp project with cross-module call sites and a barrel re-export (all updated, include_exports=True), plus a naively-colliding pair where exactly one rename drops pre-batch with the reason and the survivor lands.
- uv run pytest -q: 1297 passed; ruff clean.

Risks/follow-ups: UPPER_SNAKE-for-constants detection deferred (documented); CLI wiring of the violations->pairs extraction is the noted follow-up (rename_pair is the ready-made hook).
<!-- SECTION:FINAL_SUMMARY:END -->
