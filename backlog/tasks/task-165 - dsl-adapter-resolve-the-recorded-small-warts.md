---
id: TASK-165
title: 'dsl/adapter: resolve the recorded small warts'
status: Done
assignee:
  - '@claude'
created_date: '2026-08-09 00:17'
updated_date: '2026-08-09 01:27'
labels:
  - dsl
  - cleanup
dependencies:
  - TASK-161
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Three follow-ups recorded-not-actioned during the phase-3 and cleanup arcs: (1) expr.py Compare: matches/startswith (and is_within) silently return False for a non-str, non-Expr rhs — refuse at construction like is_in already does (a tuple rhs for startswith used to work via Python semantics and now silently never fires; a non-str matches used to raise). (2) adapters/python_adapter.py get_visibility returns tuple[Visibility, Confidence] but after TASK-161 the confidence element is read only by its own tests — narrow the signature to Visibility, updating the four assertions in tests/test_python_adapter.py (SANCTIONED test edit: the tuple shape is the thing deliberately changing). (3) The module-less-file edge from the phase-3b ledger entry: dsl/universes.py _Env.of substitutes file_path for a missing MODULE symbol while the frozen visibility rules skip such files (module_id is None -> continue) and dsl/columns.py's _modules_by_file correctly ports the None-skip — make the row side skip module-less files too, so the port agrees with itself and with the frozen engine; update the ledger entry from 'unreconciled' to reconciled in the same change. Parity-neutral on all four targets (every indexed file there has a MODULE symbol).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A non-str non-Expr rhs for matches/startswith/is_within refuses at construction naming the alternative
- [x] #2 get_visibility returns Visibility alone and no caller unpacks a tuple
- [x] #3 Symbol rows from module-less files no longer reach the visibility candidate clauses; the ledger entry is updated to reconciled
- [x] #4 Full gate green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v4 in /home/user/pypeeker-wt165, parallel with TASK-158.
<!-- SECTION:PLAN:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Resolved all three warts (PR #130).

- Compare refuses wrong-shaped rhs at construction (matches/is_within: str or Expr; startswith: str, tuple-of-str, or Expr); tuple-of-prefixes startswith SUPPORTED as one read — the shape the isinstance narrowing had silently killed.
- get_visibility narrowed to Visibility; eight binder unpacks simplified; four sanctioned test-assertion updates. Advisory recorded (stale-adapter tuple now accepted silently — a seam for the adapter-contract work the flip forces anyway).
- Module-less-file edge reconciled structurally: MODULE_FILES ProjectedSet wired into all five family rules at the frozen guard's exact position. Scout proved the wart was masked-but-fragile; ledger amended to reconciled, crediting the structural wired-clause test as the lock after a rollback experiment showed the behavioural tests pass without the fix.

Parity-neutral (unchanged counts on all four targets); 31 new/updated tests (3,429 total); gate PASS in worktree and after merge.
<!-- SECTION:FINAL_SUMMARY:END -->
