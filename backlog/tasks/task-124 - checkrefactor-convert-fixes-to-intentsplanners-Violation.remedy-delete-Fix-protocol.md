---
id: TASK-124
title: >-
  check+refactor: convert fixes to intents+planners; Violation.remedy; delete
  Fix protocol
status: Done
assignee:
  - '@claude'
created_date: '2026-07-31 04:51'
updated_date: '2026-07-31 17:14'
labels:
  - check
  - refactor
  - architecture
dependencies:
  - TASK-122
  - TASK-123
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 4: each of the five fixes becomes a refactor planner with a real intent (delete-symbol, remove-import, rewrite-star-import, tuplify, docstring-param via ReplaceTextIntent). Violation.fix becomes Violation.remedy: Intent|None. Delete check/fixes.py, check/protocols.py, FixIntent. check may import intents (leaf) but still never refactor.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Five planners exist in refactor/ with real footprints/effects; rules attach remedy intents; check --fix routes violations' remedies through the batch engine.
- [x] #2 Fix, FixPlan, FixDeclined, DeclineReason, FixIntent are deleted; no fix-protocol code remains; full gate green.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Stage A (sonnet): five new intent kinds + planners in refactor/ ported from the fix implementations (delete-symbol executor at last, remove-import, rewrite-star-import, tuplify, replace-text for docstring-param), registered via @register_planner; Anchor introduced with real consumers only; old Fix protocol still alive in parallel.
Stage B (opus): Violation.fix -> Violation.remedy: Intent|None; rules attach intents; check gains intents in boundaries; app/check_fixes routes remedies through the batch engine preserving the check --fix report contract (fix_id/reason slugs byte-identical); port protocol-level tests to planner tests; delete Fix/FixPlan/FixDeclined/DeclineReason/FixIntent/PlannableFix/with_fix and check/fixes.py+protocols.py; port FixIntent consumers in plan-batch/app.
Then gate + 3-lens opus review (report-contract, deletion-completeness/architecture, ported-test equivalence) + fix + final gate. CLI-level tests are the frozen oracle and must not change.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Also introduces the Anchor union (SymbolAnchor | RangeAnchor | EdgeAnchor) in intents/, deferred from phase 1 because an export with no src consumer trips the unused-public-symbol gate; Violation.remedy is its first consumer.

Two-stage workflow (sonnet stage A: five planners ported byte-for-byte with legacy refusal slugs on MaterializeError.code; opus stage B: remedy cutover + deletions). Anchor union landed with real consumers (SymbolAnchor/RangeAnchor; EdgeAnchor deliberately deferred — no consumer). delete-symbol finally has a real executor; its two v1 no-planner tests were deliberately ported.

Adversarial review: 11 findings, 6 must-fix, all around contract fidelity the implementers missed: docstring-drift lost its stale-index refusal (ReplaceTextPlanner had no freshness gate), changed its report description, and silently dropped ambiguous renames that used to appear as declined entries; plus stale docstrings claiming delete-symbol had no planner. Opus fixer applied all six; final gate 1536 pytest, ruff clean, self-lint exit 0. Advisory notes for follow-up: RewriteStarImportIntent cross-file read declaration is conservative-but-debated; plan-batch executed[].kind for fix sweeps now shows concrete kinds instead of the opaque "edit" (deliberate consequence of FixIntent deletion).

Orchestrator re-verified: deletions complete (grep zero hits), boundaries correct (check gained intents, still no refactor), full gate green.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Convert the five fixes to intents + planners, replace Violation.fix with Violation.remedy, and delete the Fix protocol (phase 4 — the core of the four-noun architecture).

What changed:
- Five new/completed intent kinds with real footprints/effects and registered planners: delete-symbol (finally executable), remove-import, rewrite-star-import, tuplify, replace-text — each porting its fix predecessor logic byte-for-byte, with refusals carrying the legacy reason slugs via MaterializeError.code.
- Anchor union (SymbolAnchor | RangeAnchor) in intents/anchors.py, consumed by every new planner signature.
- Violation.remedy: Intent | None (with_remedy in check/models.py); rules attach intents with fix-id strings preserved exactly; check gained intents in import-boundaries and still never imports refactor.
- check --fix routes remedies through the batch machinery while keeping its JSON report byte-compatible (same keys, fix_id strings, reason slugs, conflict determinism, single combined transaction, rollback).
- DELETED: check/fixes.py, check/protocols.py, Fix/FixPlan/FixDeclined/DeclineReason/with_fix, FixIntent/PlannableFix — grep-zero across src and tests.
- Docs: architecture.md current-state sections describe remedies-as-intents; aspirational item 2 folded; CLAUDE.md gained the remedies-are-intents convention.

Adversarial review (3 opus lenses): 11 findings, 6 must-fix applied — contract-fidelity catches around docstring-drift refusal semantics and stale planner docs. Gate: 1536 pytest passed, ruff clean, self-lint exit 0; CLI-level oracle tests unmodified.
<!-- SECTION:FINAL_SUMMARY:END -->
