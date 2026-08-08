---
id: TASK-154
title: >-
  dsl: port the visibility and reference-counting family including the barrel
  semi-join
status: Done
assignee:
  - '@claude'
created_date: '2026-08-03 18:11'
updated_date: '2026-08-08 17:46'
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
- [x] #1 The barrel exemption is expressed as a semi-join and produces identical exemptions to the old engine on this repo
- [x] #2 Dynamic-access weakening applies to exactly the same rule set as today
- [x] #3 Each ported rule reaches differential parity or carries a ledger entry
- [x] #4 Full gate green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v4 in /home/user/pypeeker-wt154, parallel with TASK-155 (disjoint rule families; both extend dsl/rules.py — merge order adjudicated by orchestrator).
<!-- SECTION:PLAN:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Ported the visibility/reference-counting family as semi-joins over projected id columns (PR #125).

Changes:
- dsl/columns.py (projected id columns), dsl/joins.py (ProjectedSet/SemiJoin/in_set/Weaken with structural cache keys via Corpus.memo), dsl/visibility.py (the five rules — over-exposed-export is the fifth dynamic-access-weakening consumer, enumerated from frozen call sites).
- Barrel exemption as a semi-join, materialized once per run; frozen inline set and projected set are set-identical on this repo.
- EvalContext gains corpus/subject/universe with loud refusal outside selections; consuming_falsity via dataclasses.replace.

Evidence: over-exposed-export 166-vs-166, over-exposed-module-symbol 9-vs-9, unused-public-symbol 1-vs-1 (heuristic — weakening exercised), 0-vs-0 rules pinned by unit tests. Six ledger entries, one declared divergence (test-only message drops its count).

Orchestrator: hand-fixed stale ledger measurements, ledgered the module-less-file edge (TASK-158), made _visibility_table refuse non-mapping options loudly; merge-unified Corpus.materialize into Corpus.memo against phase 3c stage 1.

Tests: 126 new (3,285 total); four-step gate PASS with 8 rules genuinely compared.
<!-- SECTION:FINAL_SUMMARY:END -->
