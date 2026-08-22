---
id: TASK-156
title: 'dsl: mutation terminals and expression-driven fixes'
status: Done
assignee:
  - '@claude'
created_date: '2026-08-03 18:11'
updated_date: '2026-08-17 14:03'
labels: []
dependencies:
  - TASK-153
  - TASK-154
  - TASK-155
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 4 of the DSL rewrite (dsl-rewrite.md is normative). Mutation values: named top-level objects carrying kind, params, preconditions, and the confidence floor; an application operator taking no options that yields intents into the existing batch machinery; demote and privatize as two selections over ONE shared mutation value; evidence-typed anchors making the CLI-typed-id-is-DECLARED rule real; check --fix driven through the new engine behind the differential gate; v1 composites resolve to existing planner kinds only, erroring at construction.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 demote and privatize share one mutation value; the confidence floor is an attribute of that value; a CLI-typed id applies where a heuristic finding refuses
- [x] #2 Application yields flat unordered intents consumed by the existing batch scheduler unchanged
- [x] #3 check --fix through the new engine is differentially identical to the old path or ledgered
- [x] #4 Full gate green
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Built the DSL write half: mutation terminals, expression-driven fixes, and a fix-level differential oracle. Phase 4 complete; the flip (TASK-157) is unblocked with both engines still gradeable against each other.

Changes:
- dsl/terminals.py: the Mutation value — kind, params, preconditions, and the confidence FLOOR as an attribute (fork #2), with construction-time refusals for an unplannable kind (fork #10, PLANNED_KINDS mirrors the 12 registered planners), an intent_id override (fork #5), unknown/missing params, and a non-pointwise precondition. Six named values incl. the shared DEMOTE.
- dsl/demotion.py: demote and privatize as two selections over ONE shared DEMOTE object, bound in src rather than by convention, so fork #2 is structural. A CLI-typed id is DECLARED (fork #12) and clears DEMOTE INFERRED floor where a dynamic-access-weakened nomination is refused — the AC #1 behaviour, now reached through the evidence lattice instead of a skip_heuristic flag.
- Application: selection x mutation as a value; decisions() carries the refusal reason so an empty intent tuple never conflates no rows matched with every row refused.
- fix_id derived as <origin>:<mutation>:<anchor>, which reproduces all three frozen remedy ids character for character — so the derivation landed here rather than at the flip and is graded while the old path exists.
- Restored the three dropped remedies (unused-imports, star-imports, docstring-drift), closing the phase-3d ledger entry; Finding.remedy mirrors Violation.remedy (compare=False).
- app/intent_fixes.py + a fix pass in the oracle: target=self fix old=18 new=18 declined=1/1 edits=36/36, byte-identical planned edits.
- Layering: dsl gained the intents allowance (mirroring check); still never imports check or refactor. CLAUDE.md updated.

Post-review hand-fixes (each verified to fire before landing, each pinned by a new test): Application was directly constructible around require_mutation_fields, and Mutation.field_reads ignored an opaque precondition declared reads — both were the silent-empty class fork #12 exists to kill, now closed at the value constructor and via a universe-field vocabulary that still ignores free-form prose reads; a mutation omitting a REQUIRED param constructed fine and died with a TypeError on the first row, now refused at construction; demote/privatize now bind DEMOTE in src. The pipeline separately closed a vacuous-green hole where dropping fix-engine from the manifest disabled all write-half grading and still exited PASS.

Gate: verify-repo.sh PASS on all four steps (3723 pytest, ruff, baseline-free self-lint, differential oracle over 8 targets x 22 rules PLUS the new fix pass), run independently after every fix.
<!-- SECTION:FINAL_SUMMARY:END -->
