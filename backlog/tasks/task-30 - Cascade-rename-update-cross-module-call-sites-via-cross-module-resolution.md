---
id: TASK-30
title: 'Cascade rename: update cross-module call sites via cross-module resolution'
status: Done
assignee:
  - '@claude'
created_date: '2026-05-23 22:44'
updated_date: '2026-05-23 22:50'
labels:
  - refactor
  - index
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Chunk 4 (capstone) of the layered rebuild, building on cross-module resolution (TASK-29). Today plan-rename updates the definition, same-module references, and import statements, but NOT consumer call sites in other modules: those bind to the local import symbol id, so find_references misses them and the rename leaves broken code (e.g. 'from lib import do_help' alongside an un-renamed 'helper()' call). This task switches the planner to find_all_references so non-aliased cross-module usages are renamed too. Aliased usages are left untouched (the existing text-match guard skips them because their token differs from the old name); import statements continue to be handled as before, including the --include-exports __init__ behavior.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 plan-rename updates non-aliased consumer call sites in other modules (e.g. helper() in main.py is renamed when its definition lib:helper is renamed), in addition to the definition, same-module references, and import statements
- [x] #2 Aliased cross-module usages are preserved: renaming lib:helper to do_help updates 'from lib import helper as h' (the helper token) but leaves the alias h and its call sites h() unchanged
- [x] #3 Existing import-statement and --include-exports/__init__ behavior is unchanged; external/stdlib imports are still unaffected; no duplicate edits are produced
- [x] #4 test_plan_cross_file is corrected to expect the consumer call site edit (edit_count 3), and new tests prove consumer call sites and multi-file call sites are rewritten while aliases are preserved
- [x] #5 Full suite green; pypeeker check exits 0
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. planner.plan(): replace find_references(symbol.symbol_id) with find_all_references(symbol.symbol_id) so cross-module call sites are collected. Aliased usages auto-skipped by the existing actual_text==old_name guard in _build_edits; import statements still via find_import_symbols (unchanged).
2. Update test_plan_cross_file edit_count 2->3 and assert the main.py call site is rewritten.
3. Add tests: consumer call site renamed end-to-end (content check), multi-file call sites, alias call sites preserved (h() untouched), barrel-consumer call site left as documented follow-up.
4. Run full suite + pypeeker check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
One-line core change: planner.plan now calls find_all_references instead of find_references, so cross-module call sites are collected. Aliased usages are dropped by the pre-existing actual_text==old_name guard in _build_edits, so a consumer\'s alias and its call sites are left intact; import-statement and --include-exports/__init__ handling is unchanged.

Verified end-to-end (index->plan->apply on a temp project): renaming lib:helper to do_help rewrote both the import and the do_help() call site in main.py, yielding runnable code.

Tests: 393 pass. Corrected test_plan_cross_file (edit_count 2->3) and added TestCrossModuleCallSiteCascade (consumer call site renamed, multi-file call sites = 6 edits, alias call sites preserved = 1 edit). pypeeker check exits 0.

Follow-up: a consumer importing via a package __init__ barrel (from pkg import X) and calling X() will get its call site renamed but its barrel import is not yet updated by import discovery (find_import_symbols matches the direct module path only); covering that requires resolver-based import discovery and is left as a follow-up.

Implementation correction (post-review): the planner does NOT use find_all_references (the resolver), because that over-reaches to barrel-consumer call sites whose import statement is not being updated, leaving a module half-renamed. Instead the cascade is binding-aware: an import is its own symbol (kind IMPORT, e.g. main:helper) distinct from the canonical definition, and a consumer call site binds to that local import symbol. So the planner gathers references whose symbol_id is the definition OR one of the import symbols being renamed (binding_ids = {def} U {imports_to_edit}), then renames those. This keeps every module internally consistent: a call site is renamed only when its binding import is also renamed. Aliased imports are still renamed at the import token; their alias call sites are dropped by the actual_text==old_name guard.

Added TestCrossModuleCallSiteCascade.test_barrel_consumer_left_consistent: renaming pkg.lib:make through a package barrel (app.py does `from pkg import make; make()`) leaves app.py untouched (files_affected == [pkg/lib.py]) rather than renaming the call but not the import.

394 tests pass; pypeeker check exits 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Made plan-rename cascade across modules: a definition rename now also rewrites non-aliased consumer call sites in other files, not just the definition, same-module references, and import statements.

What changed:
- planner.plan switches from find_references to find_all_references (the cross-module resolver from TASK-29). Aliased usages are preserved automatically because _build_edits already skips any location whose token != old_name, so a consumer\'s chosen alias (h) and its call sites stay put while the import token (helper) is renamed.

User impact: fixes a latent bug where renaming a definition left consumer call sites pointing at the old name (broken code). Confirmed end-to-end: from lib import helper; helper() becomes from lib import do_help; do_help().

Tests: 393 pass. test_plan_cross_file corrected to edit_count 3; new TestCrossModuleCallSiteCascade covers consumer call-site rename, multi-file call sites, and alias preservation. pypeeker check exits 0.

Follow-up/risk: barrel-imported consumers (from pkg import X via __init__ re-export) get call sites renamed but not their import line yet; closing that needs resolver-based import discovery (documented in notes).

Implementation note: the cascade is binding-aware rather than resolver-based. Because an import is its own symbol distinct from the definition, the planner renames references bound to the definition or to an import it is already renaming (binding_ids union), which keeps each module internally consistent and avoids half-renaming barrel consumers. The cross-module resolver (find_all_references) remains available for queries (pypeeker refs --all).
<!-- SECTION:FINAL_SUMMARY:END -->
