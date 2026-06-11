---
id: TASK-87
title: 'refactor: intent/effect/footprint protocol + anchor remapping'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 18:27'
updated_date: '2026-06-11 20:43'
labels:
  - refactor
  - m3-planner
dependencies:
  - TASK-85
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Composite plans are lists of intents (transform + semantic anchor + options), not byte edits. Each transform declares reads (symbols, files, derived facts) and effects (files written; ids created/deleted/renamed with mappings). Renames produce id substitutions applied to downstream intents' anchors. Conflict = write/write or write/read footprint intersection.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Intent, Effect, and Footprint types exist; rename/inline/extract/delete-style transforms can be expressed as intents with declared footprints and effects
- [x] #2 Anchor remapping rewrites pending intents through rename/delete effects (delete orphans dependents with a reported reason)
- [x] #3 Conflict detection between two intents is a pure function with tests covering rename-vs-edit, delete-vs-rename, and disjoint cases
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Read preconditions/planner/extract/inline/simulate/overlay + symbol_id grammar (done)
2. New refactor/footprint.py: prefix-aware symbol-id containment (affects), Footprint (frozen, frozensets), ConflictKind/ConflictReport, pure conflicts_with; Effect (renamed/deleted/created/files_written/files_renamed) with remap_id/remap_file and then() composition
3. New refactor/intents.py: Intent base (frozen dataclass, intent_id/deps), OrphanReason/OrphanedIntent, PlannableFix structural Protocol (no check import), concrete RenameIntent/ExtractVariableIntent/ExtractMethodIntent/InlineVariableIntent/DeleteSymbolIntent/FixIntent with footprint(store), predicted_effect(store), remap(effect)
4. tests/test_intents.py: conflict matrix (rename-vs-edit, delete-vs-rename, disjoint, m:Foo vs m:Foobar trap), remapping edge cases (rename-of-rename, delete-vs-rename, rename-vs-delete, prefix descent), orphan reasons, FixIntent duck-typing, effect composition, frozen/hashable determinism
5. uv run pytest -q + ruff check; check ACs, final summary, Done
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Agent completed implementation (refactor/footprint.py + refactor/intents.py + 66 tests) but hit a session limit before bookkeeping; orchestrator verified: full suite 1079 passed, ruff clean, self-lint green. Footprint is prefix-aware over the symbol-id grammar; Effect carries renamed/deleted/created maps; intents wrap the four planners plus a duck-typed FixIntent (no check import, layering preserved); remapping covers rename-of-rename composition, delete-orphaning, and prefix descent.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Intent/effect/footprint protocol for the batch planner: frozen Footprint with prefix-aware conflict detection (write/write, write/read over symbols, files, fact keys), Effect with composable rename substitutions, Intent wrappers for rename/extract/inline/delete plus FixIntent (structural typing to avoid check->refactor layering inversion), and anchor remapping incl. orphan reasons. 66 unit tests.
<!-- SECTION:FINAL_SUMMARY:END -->
