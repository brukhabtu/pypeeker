---
id: TASK-115
title: 'refactor: promote habit TYPE_CHECKING guards to runtime imports'
status: Done
assignee:
  - '@claude'
created_date: '2026-07-30 22:57'
updated_date: '2026-07-30 23:00'
labels:
  - refactor
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Several modules keep type-only imports under 'if TYPE_CHECKING:' out of habit, not because they break a real cycle. Now that no-import-cycles (TASK-114) enforces module-level acyclicity, these habit guards can be promoted to plain runtime imports, expressing the dependency honestly. Only guards whose target is a strictly lower layer (models/storage/query), a third-party module (tree_sitter), or an acyclic sibling (refactor.dataflow) are in scope; any guard that genuinely breaks a cycle stays. The no-import-cycles self-lint is the backstop that proves no promotion introduces a load-time cycle.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 TYPE_CHECKING-only import blocks in check/fixes.py, analysis/hierarchy.py, refactor/{intents,inline,preconditions,extract,simulate}.py are promoted to runtime imports where the target is a lower layer, third-party, or an acyclic sibling; no guard that breaks a real cycle is removed.
- [x] #2 The full gate stays green: pytest, ruff, and pypeeker check (including no-import-cycles) all pass, proving no promotion introduced a module-load cycle.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. For each file, move the type-only imports out of the if TYPE_CHECKING block into runtime imports (merging into existing runtime import lines where possible).
2. Remove now-empty TYPE_CHECKING blocks and unused TYPE_CHECKING imports.
3. Run full gate (pytest, ruff, pypeeker check with no-import-cycles) as the backstop; revert any promotion that introduces a cycle.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Promoted type-only imports out of TYPE_CHECKING blocks in 7 modules: check/fixes.py (FileIndex), analysis/hierarchy.py (FileIndex, Reference, IndexStore), refactor/intents.py (Symbol, IndexStore), refactor/inline.py (tree_sitter.Node), refactor/preconditions.py (Node, FileIndex/Scope/Symbol, SemanticQueryEngine, RangeDataFlow, IndexStore), refactor/extract.py (Node, Scope, RangeDataFlow), refactor/simulate.py (IndexStore). Each target is a strictly lower layer (models/storage/query), third-party (tree_sitter), or an already-runtime-imported sibling (refactor.dataflow), so none can close a load-time cycle.

The no-import-cycles self-lint is the proof: pypeeker check exits 0 after the promotions, confirming every removed guard was habit, not load-bearing. Gate: 1398 pytest passed, ruff clean, check exit 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Promote habit `TYPE_CHECKING` guards to plain runtime imports across 7 modules, now that `no-import-cycles` (TASK-114) can prove none of them was breaking a real cycle.

## What changed
Moved type-only imports out of `if TYPE_CHECKING:` blocks in `check/fixes.py`, `analysis/hierarchy.py`, and `refactor/{intents,inline,preconditions,extract,simulate}.py`, merging into existing runtime import lines where possible and dropping the now-unused `TYPE_CHECKING` name from each `typing` import.

## Why it is safe
Every promoted import targets a strictly lower layer (`models`/`storage`/`query`), a third-party module (`tree_sitter`), or a sibling already imported at runtime (`refactor.dataflow`) — none can form a module-load cycle. The `no-import-cycles` self-lint is the backstop: `pypeeker check` exits 0 after the change, empirically confirming these guards were ceremony, not load-bearing cycle-breakers.

## Tests
Full gate green: 1398 pytest passed, ruff clean, `pypeeker index src && pypeeker check` (incl. no-import-cycles) exit 0.
<!-- SECTION:FINAL_SUMMARY:END -->
