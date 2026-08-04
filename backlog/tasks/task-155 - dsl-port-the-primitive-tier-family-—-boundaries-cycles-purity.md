---
id: TASK-155
title: 'dsl: port the primitive-tier family — boundaries, cycles, purity'
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
Phase 3c of the DSL rewrite (dsl-rewrite.md is normative). The rules needing primitive traits: import-boundaries (strict mode, allow-table, report-unused-allowances, representative-file), no-import-cycles (the deferred-import semantics in the ledger spec note: every enclosing scope up to module must be module or class body; function, lambda, AND comprehension anywhere on the chain defer — preserve exactly), the purity family, no-unresolved-refs. Primitive traits are hand-written Python declaring their own confidence, registered like today's.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 import-boundaries ports with strict mode, unused-allowance reporting, and representative-file selection intact
- [ ] #2 no-import-cycles preserves the exact deferred-import semantics in the dsl-rewrite.md spec note
- [ ] #3 Each ported rule reaches differential parity or carries a ledger entry
- [ ] #4 Full gate green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v4 in /home/user/pypeeker-wt155, parallel with TASK-154.
<!-- SECTION:PLAN:END -->
