---
id: TASK-171
title: 'binder: decide whether per-file symbol ids must be unique'
status: To Do
assignee: []
created_date: '2026-09-05 15:01'
labels:
  - architecture
dependencies:
  - TASK-157
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Same-named def bodies (conditional redefinition, overload stubs) bind their locals to one colliding id (e.g. mod:f:x twice) with no $N suffix. As a consequence, analysis.symbols.symbols_by_id now carries two named election policies (first-wins for type_annotation, last-wins for calls/writes to match the frozen rules and dsl/universes), pinned in tests/test_module_id_collisions.py and tests/test_analysis_symbols.py. This split-policy behavior should be a deliberate, documented decision rather than an emergent workaround.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A decision is recorded in architecture.md: either symbol ids are made unique via $N suffixing, or the collision semantics are documented as intentional
- [ ] #2 If unique ids are chosen, the first-wins and last-wins election policies collapse to one, and tests/test_module_id_collisions.py and tests/test_analysis_symbols.py are updated accordingly
- [ ] #3 If a dsl-rewrite.md ledger entry is needed because frozen output moves, it is added
<!-- AC:END -->
