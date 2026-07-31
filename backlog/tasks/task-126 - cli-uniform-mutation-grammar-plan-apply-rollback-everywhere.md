---
id: TASK-126
title: 'cli: uniform mutation grammar (--plan / apply / rollback everywhere)'
status: Done
assignee:
  - '@claude'
created_date: '2026-07-31 04:51'
updated_date: '2026-07-31 18:54'
labels:
  - cli
  - architecture
dependencies:
  - TASK-123
  - TASK-124
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 6: every mutating command applies by default with --plan for plan-only; apply/rollback/transactions work identically across ops; deliberate breaking CLI change with doc updates.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 rename/extract/inline/privatize/check --fix share the grammar; docs updated; full gate green.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Grammar: mutating commands drop the plan- prefix (rename, inline-variable, extract-variable, extract-method; batch replaces plan-batch), APPLY by default, --plan for plan-only; demote/promote/privatize/check --fix join the same grammar; apply/rollback/transactions unchanged. Output: TransactionSummary JSON + applied:true when applied (privatize precedent); --plan emits today plan-only payload. Old plan-* names removed (deliberate pre-1.0 break). Docs: architecture.md Output contract section revised for the new grammar; CLAUDE.md/README examples updated. Tests updated to the new grammar comprehensively. Workflow: sonnet implement, haiku gate, 3 opus lenses (grammar-consistency, apply/rollback safety, test-migration completeness), fixer.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Workflow (sonnet implement, haiku gates, 3 opus lenses, opus fixer): 15 findings, 6 must-fix. Standouts: the claimed apply-failure contract was wrong — pre-flight failure leaves the tx PENDING/re-appliable but a mid-apply failure marks it FAILED and terminal; docs corrected and both branches pinned by new tests (incl. an ENOSPC mid-write simulation). A dead plan-rename string in applier.py runtime output was caught. check --fix gained --plan for full grammar uniformity, with the plan-only path writing the same single PENDING check-fix transaction and touching nothing.

Grammar is enforced structurally: _plan_option + _finish_mutation + _submit_and_finish are the only mutation tail — per-command code is intent construction only. privatize --apply removed; batch replaces plan-batch. Orchestrator verified: old commands absent from --help, 1567 pytest, ruff clean, self-lint exit 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Uniform mutation grammar (phase 6, deliberate pre-1.0 break): every mutating command applies by default with --plan for plan-only.

What changed:
- plan-rename/plan-inline-variable/plan-extract-variable/plan-extract-method/plan-batch became rename/inline-variable/extract-variable/extract-method/batch; demote/promote/privatize joined the same default (privatize --apply removed); check --fix gained --plan. apply/rollback/transactions unchanged.
- One shared code path (_plan_option, _submit_and_finish, _finish_mutation) owns the grammar so it cannot drift per-command; apply goes through the standard TransactionApplier; on success the payload gains applied:true, on failure the standard apply-failed envelope with the transaction left in the applier truthful state (PENDING pre-flight, FAILED mid-apply — both now documented correctly and test-pinned).
- Docs: Output contract section rewritten honestly around the one deliberate break; all examples updated; stray plan-rename references (including one in applier runtime output) purged.
- Tests migrated comprehensively: every old scenario kept under the new grammar, both modes covered per command, a real CLI-level coverage gap for extract/inline closed, apply-failure branches pinned.

Gate: 1567 pytest passed, ruff clean, self-lint exit 0.
<!-- SECTION:FINAL_SUMMARY:END -->
