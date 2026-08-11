---
id: TASK-167
title: >-
  dsl phase 3e: receiver_chain on the references universe; port the mutation
  pair
status: In Progress
assignee:
  - '@claude'
created_date: '2026-08-09 02:33'
updated_date: '2026-08-11 12:31'
labels:
  - dsl
dependencies:
  - TASK-158
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
no-argument-mutation and no-hidden-global-mutation emit one violation per mutation site (a Reference) and need receiver_chain plus the enclosing FUNCTION/METHOD symbol and a receiver-root name lookup (frozen shapes: check/builtin/no_argument_mutation.py, no_hidden_global_mutation.py, _mutation_detail). Publish receiver_chain (and whatever of receiver_root_symbol_id/enclosing-function the port needs) on the references universe — a deliberate universe-surface extension, documented — then port both rules at parity with fixture targets (both are 145/5 findings on this repo per the TASK-154 scout, so self-target grades are nonzero already).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The references universe publishes the fields the mutation rules need, documented
- [ ] #2 Both rules claimed at parity
- [ ] #3 Full gate green
<!-- AC:END -->
