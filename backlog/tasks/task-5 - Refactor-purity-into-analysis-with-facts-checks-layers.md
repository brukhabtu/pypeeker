---
id: TASK-5
title: Refactor purity into analysis/ with facts + checks layers
status: Done
assignee:
  - '@claude'
created_date: '2026-04-30 02:55'
updated_date: '2026-04-30 02:58'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Split monolithic PurityChecker into a fact layer (typed observations on the index) and a check layer (composite verdicts). Facts are reusable across future checks (determinism, side-effects, thread-safety). Move src/pypeeker/purity/ contents into src/pypeeker/analysis/ with facts/ and checks/ subpackages. Per-check policies (like local-variable suppression for purity) move out of the fact layer and into the check that owns them.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 src/pypeeker/analysis/ package created with context.py, facts/, checks/ subpackages
- [x] #2 AnalysisContext dataclass with for_function() factory replaces the inline setup in PurityChecker.check
- [x] #3 Typed fact dataclasses: OuterScopeWrite, AttributeWrite, ImpureBuiltinCall, AttributeMethodCall (with receiver_is_local_variable boolean)
- [x] #4 Fact extractors are pure functions: find_outer_scope_writes, find_attribute_writes, find_impure_builtin_calls, find_attribute_method_calls
- [x] #5 Local-variable suppression moves OUT of the fact layer and INTO the purity check (other future checks can choose a different policy)
- [x] #6 src/pypeeker/purity/ removed; tests updated to import from pypeeker.analysis
- [x] #7 All 18 purity tests still pass; full suite remains green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Create analysis/context.py with AnalysisContext dataclass + for_function() factory\n2. Create analysis/facts/models.py with typed fact dataclasses\n3. Create analysis/facts/writes.py and analysis/facts/calls.py with pure-function extractors\n4. Create analysis/checks/purity.py that composes facts -> PurityResult, applies purity-specific policy (local-var suppression)\n5. Update analysis/__init__.py to expose PurityChecker / check_purity\n6. Update tests to import from pypeeker.analysis\n7. Delete src/pypeeker/purity/\n8. Run full test suite
<!-- SECTION:PLAN:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Refactored purity check into a layered analysis/ package separating semantic atoms from composite verdicts.

New layout:
- analysis/context.py: AnalysisContext (frozen dataclass) + for_function() factory; ContextError for resolution failures.
- analysis/facts/models.py: typed fact dataclasses (OuterScopeWrite, AttributeWrite, ImpureBuiltinCall, AttributeMethodCall).
- analysis/facts/writes.py + calls.py: pure-function extractors that take an AnalysisContext + (for calls) a denylist, returning typed facts.
- analysis/checks/purity.py: composes facts into PurityResult; applies purity-specific policy (skip AttributeMethodCall facts whose receiver is a local variable). Re-exports PurityChecker for stateful batching.
- analysis/checks/_purity_denylists.py: IMPURE_BUILTINS / IMPURE_ATTRIBUTE_NAMES (purity-specific policy, not generic facts).

Key design decisions:
- Facts are check-agnostic: AttributeMethodCall reports receiver_is_local_variable; the check decides what to do with it. A future ThreadSafetyCheck could choose the opposite policy.
- Denylists are passed as parameters into fact extractors, not hardcoded — future checks can subset (e.g. just non-determinism names).
- Removed src/pypeeker/purity/ entirely; tests import from pypeeker.analysis.

Tests: 18 purity tests still pass against new architecture; added 15 new fact-layer tests in tests/test_analysis_facts.py covering AnalysisContext factory, all four extractors in isolation, and the receiver_is_local_variable flag for both local-var and parameter receivers. Full suite 205/205 passing. Analysis package coverage 95%.
<!-- SECTION:FINAL_SUMMARY:END -->
