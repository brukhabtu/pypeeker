---
id: TASK-154
title: >-
  dsl: port the visibility and reference-counting family including the barrel
  semi-join
status: In Progress
assignee:
  - '@claude'
created_date: '2026-08-03 18:11'
updated_date: '2026-08-04 04:28'
labels: []
dependencies:
  - TASK-152
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 3b of the DSL rewrite (dsl-rewrite.md is normative). The reference-counting rules: unused-public-symbol (including the barrel-export exemption as a semi-join on one projected id column, materialized once per run), over-exposed-module-symbol, born-private, test-only-production-code, and the dynamic-access confidence weakening exactly as the old engine scopes it (five consumers, not all resolve_definition callers). Parity manifest plus ledger discipline as in phase 3a.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The barrel exemption is expressed as a semi-join and produces identical exemptions to the old engine on this repo
- [ ] #2 Dynamic-access weakening applies to exactly the same rule set as today
- [ ] #3 Each ported rule reaches differential parity or carries a ledger entry
- [ ] #4 Full gate green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v4 in /home/user/pypeeker-wt154, parallel with TASK-155 (disjoint rule families; both extend dsl/rules.py — merge order adjudicated by orchestrator).
<!-- SECTION:PLAN:END -->
