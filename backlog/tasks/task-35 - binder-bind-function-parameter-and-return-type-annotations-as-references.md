---
id: TASK-35
title: 'binder: bind function parameter and return type annotations as references'
status: Done
assignee:
  - '@claude'
created_date: '2026-05-24 00:32'
updated_date: '2026-05-24 00:37'
labels:
  - binder
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Dogfooding finding. Running pypeeker on its own source surfaced ~48 "unused import" and ~94 "dead function" candidates that are almost all false positives: the codebase is actually clean. The noise traced to two reference-model gaps. This task fixes the actionable one (Gap B): identifiers in function parameter and return type annotations are not bound as references, so e.g. FileIndex reports 8 references despite 43 textual usages (~35 are annotations).

This is also a correctness bug: plan-rename of a class used in annotations misses those usages, and find_references/find_all_references under-report. Variable annotations (x: T) are already visited in assignments.py; only scopes.py (return_type and parameter types) skips visiting the annotation node. Fix: visit those annotation nodes so their identifiers register as references, resolving through normal scope/import/forward-ref rules.

(Gap A — attribute/method call resolution, e.g. store.save() — is the larger remaining gap and is left as separate future work.)
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Identifiers in function return type annotations and parameter type annotations are recorded as references (resolved via scope/import/builtins like any other name)
- [x] #2 find_references/find_all_references and plan-rename now include annotation usages of a type; renaming a class used only in annotations updates those sites
- [x] #3 Variable-annotation behavior is unchanged; forward-ref string annotations remain unbound (out of scope); module-level forward references in annotations still resolve via the existing fixup
- [x] #4 Full suite green; pypeeker check exits 0 (no new unresolved-ref violations in pypeeker's own annotations); a re-run of the dogfood unused-import probe shows the type-only false positives collapse
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
scopes.py: in visit_function_definition visit return_type_node; in visit_parameters visit the type node for typed_parameter and typed_default_parameter. Add binder tests asserting annotation identifiers produce references + a cross-file rename-through-annotation test. Run check + dogfood re-probe.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Fix in scopes.py: visit_function_definition now visits the return_type node; visit_parameters visits the type node of typed_parameter and typed_default_parameter. Variable annotations were already visited (assignments.py). Annotation identifiers resolve via normal scope/import/builtins rules and record TYPE_ANNOTATION references; subscripts (list[Widget]) bind the inner type; module-level forward refs still resolve via the existing fixup; string forward refs remain unbound (out of scope).

Dogfood-surfaced bug FIXED: enabling annotation refs made pypeeker check flag query/engine.py:143 (-> list[Location]) because Location was imported lazily inside the function body. Hoisted the import to module level (also removes the long-standing ruff F821 on that line).

Dogfooding outcome: unused-import false positives dropped 48 -> 11; FileIndex refs 8 -> 15. Verified the remaining 11 candidates are ALL still-used false positives (e.g. binder.py uses ScopeKind.MODULE / SymbolKind.MODULE as keyword-arg values whose attribute receiver is not recorded). Conclusion: the codebase has no genuinely-unused imports or dead functions to remove. The residual noise is Gap A (attribute/method receivers, esp. keyword-arg values) + function-local imports.

Tests: 412 pass (5 new binder annotation tests + a rename-through-annotation planner test). pypeeker check exits 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Bind function parameter and return type annotations as references (dogfooding finding). Identifiers in -> ReturnType and param: Type annotations were not recorded as references, so find_references/find_all_references under-reported and plan-rename of a type missed its annotation usages. scopes.py now visits the return-type and parameter-type nodes (variable annotations were already handled); subscripts like list[Widget] bind the inner type.

User impact: renaming a class now updates usages where it appears only in annotations (new test proves a consumer\'s def f(x: Widget) is rewritten). The same fix surfaced and fixed a latent bug — query/engine.py annotated -> list[Location] while importing Location lazily inside the function; the import is now module-level (also clears a stale ruff F821).

Dogfooding result that motivated this: running pypeeker on its own source reported ~48 unused imports and ~94 dead functions, almost all false positives from two reference-model gaps. This closes Gap B (annotations); after the fix the unused-import false positives fall 48 -> 11 and the remainder are confirmed still-used (Gap A: attribute/method receivers). Net: the codebase is clean — nothing to remove — and the reference graph is materially more complete.

Tests: 412 pass (5 binder annotation tests + 1 rename-through-annotation test); pypeeker check exits 0.
<!-- SECTION:FINAL_SUMMARY:END -->
