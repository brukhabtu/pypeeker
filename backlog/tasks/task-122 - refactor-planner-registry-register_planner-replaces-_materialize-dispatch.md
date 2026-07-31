---
id: TASK-122
title: 'refactor: planner registry (@register_planner) replaces _materialize dispatch'
status: Done
assignee:
  - '@claude'
created_date: '2026-07-31 04:51'
updated_date: '2026-07-31 15:04'
labels:
  - refactor
  - architecture
dependencies:
  - TASK-121
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 2: mirror @register_rule with @register_planner(intent_kind); batch._materialize becomes a registry lookup; planners self-register. Behavior-preserving.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A planner registry exists; each existing intent kind (rename, inline, extract-variable, extract-method, edit/fix, delete-symbol) resolves through it; the isinstance chain in _materialize is gone.
- [x] #2 Unknown-kind handling preserved as a registry miss; full gate green.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add a planner registry in refactor/ mirroring @register_rule: register_planner(kind) decorator mapping Intent.kind -> materializer adapter.
2. Move each _materialize branch next to its planner as a registered adapter; batch resolves through the registry; registration triggered by a side-effect import chain (mirror check/builtin).
3. Registry miss preserves the current no-executor behavior for DeleteSymbolIntent byte-for-byte.
4. New registry tests (registration, dispatch parity, unknown kind); existing tests untouched.
5. Gate + 3-lens opus adversarial review via workflow; orchestrator verifies, commits, PRs, merges.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented via the phase2-planner-registry workflow (sonnet implementer, haiku gates, 3 opus lenses). Registry in refactor/registry.py (register_planner decorator, get_materializer lookup, shared Materialized + load_transaction moved from batch); adapters live beside their planners (planner.py, inline.py, extract.py) with FixIntent/DeleteSymbolIntent adapters in new refactor/edits.py; batch.py keeps side-effect imports mirroring check/builtin discovery and reproduces the no-executor miss behavior byte-for-byte.

Adversarial review: 6 findings, 1 must-fix — the parity lens caught that dispatch was never actually invoked for extract/edit kinds (tests only checked callables existed). Fixer added 3 end-to-end dispatch tests and mutation-verified them (swapped the two extract registrations; both tests failed as they should, then reverted). Orchestrator re-verified: 1460 pytest, ruff clean, self-lint exit 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Replace batch._materialize isinstance dispatch with a planner registry, completing the one-registration-idiom item of the target architecture (phase 2).

What changed:
- New refactor/registry.py: @register_planner(kind) decorator + get_materializer(kind) lookup mirroring @register_rule (last-wins on duplicates), with the shared Materialized dataclass and load_transaction helper moved out of batch.
- Each materializer adapter now lives beside its planner: rename in planner.py, inline in inline.py, both extracts in extract.py, and FixIntent + DeleteSymbolIntent adapters in new refactor/edits.py (delete-symbol keeps its historical no-planner drop string).
- batch._materialize is a registry lookup + invoke; a true miss reproduces the exact "no executor for intent kind" detail. Registration rides on explicit side-effect imports in batch.py, mirroring check/builtin discovery. No cycles: registry imports only intents/models/storage.
- New tests/test_planner_registry.py (16 tests after review): every kind resolves, unknown-kind + delete-symbol drop parity through run_batch, duplicate registration last-wins, and end-to-end dispatch tests for rename/inline/extract-variable/extract-method/edit — the latter added by adversarial review after the parity lens caught identity-only coverage, and mutation-verified.

Behavior-preserving: 1460 pytest passed, ruff clean, pypeeker check exit 0.
<!-- SECTION:FINAL_SUMMARY:END -->
