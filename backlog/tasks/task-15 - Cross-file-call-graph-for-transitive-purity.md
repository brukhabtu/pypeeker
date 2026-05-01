---
id: TASK-15
title: Cross-file call graph for transitive purity
status: To Do
assignee: []
created_date: '2026-05-01 23:29'
updated_date: '2026-05-01 23:29'
labels: []
dependencies:
  - TASK-13
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Today, a function's purity is determined only from its own body. Calls to other project functions (e.g. self._store.save()) are silently treated as pure even when the callee is itself impure. This is the largest remaining false-negative class once typed-receiver dispatch lands. A two-pass call-graph approach: first pass classifies every function in isolation; second pass propagates impurity through call edges until fixpoint.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 New module pypeeker.analysis.call_graph: build a dict {symbol_id -> set[callee_symbol_id]} from resolved CALL references across all indexed files
- [ ] #2 Two-pass purity: (1) run check_purity on every function symbol_id, store local verdicts; (2) iterate fixpoint — if any callee is IMPURE, mark caller IMPURE with TRANSITIVE_IMPURE evidence pointing at the impure callee
- [ ] #3 New EvidenceKind.TRANSITIVE_IMPURE with target = callee symbol_id and detail = chain of callers (or just immediate callee for v1)
- [ ] #4 API: PurityChecker(store).check_with_call_graph(symbol_id) — separate entry point so callers opt in (full-codebase pass is expensive; default check stays per-function)
- [ ] #5 New end-to-end test: a wrapper function 'def wrapper(): self._store.save()' that does nothing impure directly should be flagged IMPURE with TRANSITIVE_IMPURE evidence pointing at IndexStore.save
- [ ] #6 Recursive functions terminate (visited set in the propagation loop)
<!-- AC:END -->
