---
id: TASK-63
title: 'Composition root: construct stores in CLI and inject dependencies'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 15:46'
updated_date: '2026-06-11 16:26'
labels:
  - query
  - analysis
  - cli
dependencies:
  - TASK-62
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The CLI group is supposed to be the composition root, but SemanticQueryEngine.get_tree constructs its own TreeStore from store.project_root, AnalysisContext.for_function constructs a fresh SemanticQueryEngine per call, and refactor/cst.py + applier construct PythonAdapter directly. Components constructing their own dependencies hides cost and makes cache invalidation unreasonable. Rule: stores/engines are constructed at the composition root and passed down.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 SemanticQueryEngine no longer constructs TreeStore internally (injected or provided by CLI)
- [x] #2 AnalysisContext.for_function accepts an injected engine/store rather than self-assembling a new engine per call
- [x] #3 CLI builds all stores once in the group callback and passes them down; no layer constructs a store it could receive
- [x] #4 Full test suite passes
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. SemanticQueryEngine.__init__ gains optional tree_store kwarg; default constructed once in __init__ from store.project_root; get_tree uses the injected/owned TreeStore (no ad-hoc construction).
2. AnalysisContext.for_function gains keyword-only engine param (default: build one) so callers can inject a shared engine.
3. analysis/purity.impurities builds ONE SemanticQueryEngine and passes it to every for_function call (target + each reachable function); add optional engine kwarg so the CLI can inject too.
4. cli.py: group callback builds TreeStore once into ctx.obj["tree_store"]; index command and all SemanticQueryEngine constructions use it; purity command builds one engine and passes it to for_function and impurities.
5. refactor/dataflow.analyze_range makes a single for_function call per invocation — leave as-is (note); refactor planners construct engines from injected stores, unchanged (backward-compatible signature).
6. Add tests with identity assertions that injected TreeStore/engine are actually used; run full suite.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- cli.py: group callback now builds TreeStore once into ctx.obj["tree_store"] (composition root comment added); added _engine(ctx) helper so all four query commands (symbol/refs/tree/scope) construct SemanticQueryEngine with the injected tree_store; index command reuses ctx.obj["tree_store"] instead of constructing TreeStore inline.
- purity command builds ONE engine via _engine(ctx) and injects it into both AnalysisContext.for_function and impurities (previously each built its own engine; impurities built one per reachable function).
- query/engine.py, analysis/context.py, analysis/purity.py injection changes were already in the working tree from the earlier session on this task (tree_store kwarg in __init__, engine kwarg on for_function, engine kwarg + single shared engine in impurities); verified intact.
- refactor/dataflow.analyze_range makes exactly one for_function call per invocation — left as-is per plan (no signature ripple into planners). refactor/planner.py and inline.py construct engines from CLI-injected stores (never call get_tree), unchanged per plan.
- Deliberately left (out of scope / other agents own): PythonAdapter() construction in refactor/cst.py, refactor/applier.py and indexer.py defaults — adapters are not stores and adapter-layer work is in flight under TASK-69; check/engine.py constructs no stores (verified by grep).
- Tests added: test_query_engine.py — injected TreeStore identity test (tree.json written under injected root, not store root) + backward-compat default test; test_purity.py TestEngineInjection — spy engine proves for_function uses the injected engine; monkeypatch test proves impurities with an injected engine never constructs SemanticQueryEngine anywhere in the transitive walk.
- Full suite: 613 passed, 10 skipped.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Moved store/engine construction to the CLI composition root and threaded injected dependencies through the query and analysis layers.

Changes:
- src/pypeeker/cli.py: the group callback now constructs every store exactly once (IndexStore, TransactionStore, TreeStore, adapter) into ctx.obj; new _engine(ctx) helper builds SemanticQueryEngine(store, tree_store) for the symbol/refs/tree/scope commands; the index command reuses ctx.obj["tree_store"] instead of constructing one inline; the purity command builds ONE engine and injects it into both AnalysisContext.for_function and impurities.
- src/pypeeker/query/engine.py: SemanticQueryEngine.__init__(store, tree_store=None) — TreeStore is injected, or derived once in __init__ from store.project_root for backward compat; get_tree never constructs storage ad hoc.
- src/pypeeker/analysis/context.py: for_function gains keyword-only engine param (default builds one, backward compatible).
- src/pypeeker/analysis/purity.py: impurities gains keyword-only engine param and reuses one engine for the target plus every reachable function in the transitive walk — previously a fresh engine per reachable function (the concrete perf win).

Deliberately unchanged (noted): refactor/dataflow.analyze_range makes a single for_function call; refactor planners construct engines from CLI-injected stores and never call get_tree; PythonAdapter() constructions in refactor/cst.py, applier.py and indexer.py are adapters (not stores) and adapter-layer ownership is in flight under TASK-69.

Tests:
- test_query_engine.py: injected TreeStore is actually used (tree.json lands under the injected root, not the store root) + backward-compat default coverage.
- test_purity.py TestEngineInjection: spy-engine identity assertion for for_function; monkeypatch proves impurities with an injected engine never constructs a SemanticQueryEngine during the transitive walk.
- Full suite: 613 passed, 10 skipped.

Risks: none significant — all public signatures stay backward compatible (new params optional, keyword-only where applicable).
<!-- SECTION:FINAL_SUMMARY:END -->
