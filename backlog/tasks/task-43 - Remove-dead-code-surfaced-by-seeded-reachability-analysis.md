---
id: TASK-43
title: Remove dead code surfaced by seeded reachability analysis
status: Done
assignee:
  - '@claude'
created_date: '2026-05-24 12:00'
updated_date: '2026-05-24 12:02'
labels:
  - cleanup
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Dogfooding cleanup. A seeded dead-code analysis (call graph + cross-module/receiver resolution, indexing src AND tests, seeded from CLI commands and public __init__ exports) reduced zero-reference function/method candidates from 81 to 30, cleanly bucketed (8 CLI entries, 9 protocol methods, 13 to review). Manual grep verification of the 13 separated false positives (used via patterns the graph still misses: untyped self-fields, dynamic/subscript receivers, function-return types) from genuinely dead code. This removes the verified-dead set.

Dead cluster: SemanticQueryEngine.find_reexport_locations and its only callee find_import_symbols are leftovers from the pre-TASK-31 import-rename approach (now superseded by find_importers/import_crosses_barrel). Plus four unused properties: ScopeStack.depth, ScopeStack.module_entry, TreeStore.project_root, TransactionStore.project_root.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Remove SemanticQueryEngine.find_reexport_locations and find_import_symbols (dead cluster; not referenced anywhere)
- [x] #2 Remove unused properties ScopeStack.depth, ScopeStack.module_entry, TreeStore.project_root, TransactionStore.project_root
- [x] #3 No behavior change: full suite green, pypeeker check exits 0; the test suite passing confirms the removed code was unreferenced
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Remove find_reexport_locations + find_import_symbols from engine.py (and the now-unused Location import if orphaned); remove ScopeStack.depth/module_entry; remove TreeStore.project_root + TransactionStore.project_root. suite + check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Removed verified-dead code: SemanticQueryEngine.find_reexport_locations + its only callee find_import_symbols (a dead cluster left from the pre-TASK-31 import-rename approach, superseded by find_importers/import_crosses_barrel), the now-orphaned Location import in engine.py, and four unused properties (ScopeStack.depth, ScopeStack.module_entry, TreeStore.project_root, TransactionStore.project_root) plus their dead _project_root assignments. 435 tests pass, pypeeker check exits 0, ruff clean - confirming the code was unreferenced.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Remove dead code surfaced by a seeded reachability analysis. Indexing src AND tests and seeding from CLI commands + public __init__ exports cut zero-reference function/method candidates from 81 to 30, cleanly bucketed (8 CLI entries, 9 protocol methods, 13 to review). Grep verification of the 13 separated false positives (used via untyped self-fields, dynamic/subscript receivers, and function-return types the call graph cannot follow) from genuinely dead code.

Removed: SemanticQueryEngine.find_reexport_locations and its sole callee find_import_symbols (a dead cluster from the pre-TASK-31 import-rename approach, replaced by find_importers/import_crosses_barrel), the orphaned Location import, and four unused properties (ScopeStack.depth, ScopeStack.module_entry, TreeStore.project_root, TransactionStore.project_root).

435 tests pass; pypeeker check exits 0; ruff clean - the passing suite confirms the removed code was unreferenced. Evaluation takeaway: with src+tests indexing and proper seeding, the dead-code lens is now high-signal (real findings, well-bucketed), though the residual review bucket still needs human grep-checking because the static call graph misses untyped-field, dynamic, and function-return receivers.
<!-- SECTION:FINAL_SUMMARY:END -->
