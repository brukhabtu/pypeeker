---
id: TASK-167
title: >-
  dsl phase 3e: receiver_chain on the references universe; port the mutation
  pair
status: Done
assignee:
  - '@claude'
created_date: '2026-08-09 02:33'
updated_date: '2026-08-11 14:13'
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
- [x] #1 The references universe publishes the fields the mutation rules need, documented
- [x] #2 Both rules claimed at parity
- [x] #3 Full gate green
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Ported the mutation pair (no-argument-mutation, no-hidden-global-mutation) to the DSL engine at byte parity; the manifest now claims 20 of 22 rules over 7 targets.

Changes:
- references universe grew 12 -> 23 fields (receiver_chain, four receiver_root_*, three binding_*, three enclosing_function_*), published raw as per-file model facts; _Env gained a lazy last-wins symbols_by_id and a memoized enclosing_function walk, so rules that read none of the new fields pay nothing (measured: 16.2s -> 16.4s over the 18 pre-existing rules on self).
- New dsl/mutation.py family: seven selection builders over four opaques and shared allow/enclosing-function clauses; global_rebind_rows joined sweeps.py as the tenth fact_source (the binder emits no reference for a global-redirected rebind).
- New tests/fixtures/parity/mutation corpus: the only target grading shapes 1b/2/3 of the global rule, METHOD wording, allow branches, extra-mutators, and self/cls silence.
- Grades: no-argument-mutation 150/150 on self + 10/10 on mutation; no-hidden-global-mutation 5/5 + 9/9; 0/0 elsewhere.
- Five ledger entries appended (set-collapse aggregation, last-wins shape-3 mis-naming, project-wide find_symbol resolution incl. the suffix-match trigger, file_path source, the universe extension itself). Post-lens hand-fixes: corrected the resolution entry direction (resolved-file references vs iterated-file symbol table), broadened its precondition beyond module-id collisions, added the last-wins entry, refreshed two stale manifest counts, replaced an or-"" None conflation with an explicit guard.
- Two sanctioned pre-existing test edits, both forced by the port: the manifest-claims enumeration gained the two ids, and the unported-rule refusal test swapped its example to pure-decorator-contracts.

Gate: verify-repo.sh PASS on all four steps (3591 pytest, ruff, baseline-free self-lint, differential oracle over 7 targets x 20 rules).
<!-- SECTION:FINAL_SUMMARY:END -->
