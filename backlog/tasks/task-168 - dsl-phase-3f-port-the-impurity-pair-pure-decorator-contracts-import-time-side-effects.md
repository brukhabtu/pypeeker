---
id: TASK-168
title: >-
  dsl phase 3f: port the impurity pair (pure-decorator-contracts,
  import-time-side-effects)
status: To Do
assignee: []
created_date: '2026-08-09 02:33'
updated_date: '2026-08-09 02:33'
labels:
  - dsl
dependencies:
  - TASK-158
  - TASK-167
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Both drive analysis.impurities through the same tier as the already-ported IMPURITY sweep (dsl/sweeps.py). pure-decorator-contracts flags impure @cache/@property/dunders; import-time-side-effects needs receiver_chain as well (check/builtin/import_time_side_effects.py:204) so this depends on phase 3e's universe extension. Port both at parity with fixture targets; ledger discipline as always.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Both rules claimed at parity
- [ ] #2 Full gate green
<!-- AC:END -->
