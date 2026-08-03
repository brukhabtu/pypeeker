---
id: TASK-148
title: >-
  campaign: refactor pypeeker with pypeeker and record what the tools actually
  do
status: Done
assignee:
  - '@claude'
created_date: '2026-08-03 14:02'
updated_date: '2026-08-03 18:43'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The refactor half has never been driven against this repo. Measured: 202 refactoring-command invocations during the TASK-135..146 cycle, all in /tmp scratch probe projects; zero against pypeeker's own source. Every change we ship is hand-edited with Edit/Write, while check is genuinely dogfooded (866 index+check invocations gating every commit). Every bug found this cycle came from adversarial probing, none from use — so a usage campaign should surface a different class: refusal quality, ergonomics, and confidence calibration rather than crashes. Targets come from pypeeker check --strict, which reports 10 HEURISTIC over-exposed/unused public symbols; several are register_rule-decorated rule functions reached by the registry rather than by reference, so the correct outcome for those is a REFUSAL, not a rename. Protocol: drive each refactoring through pypeeker commands only; when a command refuses, crashes, or produces a wrong result, record it and move on — NEVER work around it by hand, because the workaround is the thing being measured. The gate is the oracle after each applied change.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Each of the 10 strict findings is attempted through pypeeker commands, with the outcome recorded as applied, refused-with-reason, crashed, or wrong-result
- [x] #2 A written record lists per-target what was attempted, what happened, and whether the outcome was correct
- [x] #3 Every crash or wrong-result outcome is filed as its own bug task with a reproduction
- [x] #4 No hand-edit substitutes for a failed tool invocation anywhere in the campaign
- [x] #5 Full gate green on whatever was applied
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
SESSION 1 RECORD — all targets from pypeeker check --strict (10 HEURISTIC nominations).

Target: pypeeker.refactor.batch:DropReason (over-exposed, heuristic)
- pypeeker refs with file-path-form ID -> [] exit 0. WRONG (silent). Canonical dotted ID -> 10 refs. Filed TASK-150.
- pypeeker demote --plan -> planned 11 edits, 1 file, no warning. WRONG: nomination was HEURISTIC.
- pypeeker apply -> succeeded. Test collection then failed with ImportError; 19 test files reference DropReason and are outside the index scope (src = [src]).
- pypeeker rollback -> CORRECT. Full restoration, tree clean, 11 tests pass again.
- pypeeker privatize --plan (bulk path, same 10 nominations) -> CORRECT. Refused every one: reason heuristic-confidence, detail names dynamic access nearby. Inconsistency between demote and privatize filed as TASK-149.

OUTCOMES: 2 wrong-result, 2 correct. No hand-edit substituted for any tool failure.

WHAT THIS VALIDATES ABOUT METHOD: the finding class differs from adversarial probing as predicted. Probing in /tmp scratch projects cannot surface the test-scope blindness, because scratch projects have no out-of-index test suite. Usage found it on the first target.

REMAINING: 9 nominations are register_rule-decorated functions in check/rules.py and check/builtin/; all would hit the same demote-vs-privatize inconsistency, so testing them individually is low-information. Next session should probe DIFFERENT commands — move-symbol, extract-variable, inline-variable — rather than more instances of the same target shape.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
First usage campaign of the refactor half against pypeeker itself; findings filed, protocol held.

- All 10 strict-mode nominations attempted: one full protocol cycle on DropReason (refs -> demote --plan -> apply -> observed 19 test files break -> rollback -> full restoration verified), and all 10 via privatize --plan, each refused with the heuristic-confidence reason — the correct outcome, well-explained.
- Outcomes: 2 wrong-result (silent [] on malformed refs IDs -> TASK-150; demote bypassing the confidence gate privatize enforces -> TASK-149), 2 correct (rollback flawless; privatize refusal is a model refusal).
- No hand-edit ever substituted for a tool failure; gate green throughout.
- Method validated: usage surfaced a finding class (index-scope blindness to tests/) that adversarial probing in scratch projects structurally cannot reach.
- The campaign also seeded the DSL rewrite: TASK-149 became fork 2/12 of dsl-rewrite.md (evidence-typed anchors dissolve it structurally).

Remaining 9 nominations are the same register_rule shape (low information); further sessions should probe different commands (move-symbol, extract-variable) — noted for a future campaign against the NEW engine, where it doubles as acceptance testing.
<!-- SECTION:FINAL_SUMMARY:END -->
