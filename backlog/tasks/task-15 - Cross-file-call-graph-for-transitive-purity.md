---
id: TASK-15
title: Cross-file call graph for transitive purity
status: Done
assignee: []
created_date: '2026-05-01 23:29'
updated_date: '2026-05-02 00:12'
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
- [x] #1 New module pypeeker.analysis.call_graph: build a dict {symbol_id -> set[callee_symbol_id]} from resolved CALL references across all indexed files
- [x] #2 Two-pass purity: (1) run check_purity on every function symbol_id, store local verdicts; (2) iterate fixpoint — if any callee is IMPURE, mark caller IMPURE with TRANSITIVE_IMPURE evidence pointing at the impure callee
- [x] #3 New EvidenceKind.TRANSITIVE_IMPURE with target = callee symbol_id and detail = chain of callers (or just immediate callee for v1)
- [x] #4 API: PurityChecker(store).check_with_call_graph(symbol_id) — separate entry point so callers opt in (full-codebase pass is expensive; default check stays per-function)
- [x] #5 New end-to-end test: a wrapper function 'def wrapper(): self._store.save()' that does nothing impure directly should be flagged IMPURE with TRANSITIVE_IMPURE evidence pointing at IndexStore.save
- [x] #6 Recursive functions terminate (visited set in the propagation loop)
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added cross-file call graph (pypeeker/analysis/call_graph.py) and a transitive purity check that follows resolved CALL edges between functions.

build_call_graph(store) returns {caller_function_id -> frozenset[callee_function_id]} by:
1. Collecting all FUNCTION/METHOD symbol_ids across every indexed file
2. Walking IMPORT symbols and building a {import_symbol_id -> resolved_function_id} table by translating imported_from='lib.writer' to 'lib.py:writer' (with a fallback to 'lib/__init__.py:writer' for package imports)
3. Walking every CALL ref and emitting a caller->callee edge when both endpoints are functions, following the import_targets table to translate IMPORT-resolved calls to their original function ids
4. Skipping self-recursion edges (propagation handles cycles via visited set)

reachable_functions(graph, start) does BFS to enumerate the transitive closure.

check_purity_transitive(store, sid):
- Computes local check_purity for every reachable function
- Fixpoint loop: if any callee is IMPURE, mark caller IMPURE and record the immediate impure callee as transitive evidence
- Returns base local result if no transitive impurity discovered; otherwise emits TRANSITIVE_IMPURE_CALL evidence pointing at direct impure callees
- Locally-impure functions keep their direct evidence and append transitive callees if any

Added new EvidenceKind.TRANSITIVE_IMPURE_CALL and PurityChecker.check_with_call_graph(symbol_id) method.

Documented limitations explicitly in the call_graph module docstring: only resolved CALL edges are followed (method calls on instance fields like 'self._store.save()' are invisible without TASK-14-style typed receiver resolution); class constructor calls don't follow into __init__ (treated as opaque); first-class function passing not analyzed.

13 new tests in tests/test_purity_call_graph.py covering: intra-file edges, cross-file import resolution, self-recursion exclusion, module-level call exclusion, BFS reachability, wrapper-around-impure-helper detection, pure-chain preservation, multi-hop propagation through 3 levels, cross-file propagation, direct-impure-with-transitive-callees evidence merging, recursion termination, mutual recursion termination, PurityChecker.check_with_call_graph API.

Self-validation against pypeeker's own indexed src: build_call_graph produces 24 caller functions / 26 edges. IndexStore.is_stale is locally probably_pure (just calls self.load and compute_file_hash) but flagged IMPURE transitively via IndexStore.load — the exact false-negative class this task was designed to catch.

Full suite 270/270 passing (was 257 -> +13).
<!-- SECTION:FINAL_SUMMARY:END -->
