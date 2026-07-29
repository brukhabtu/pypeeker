---
id: TASK-112
title: 'check: adopt more rules as gates + decide policy for advisory/no-tool findings'
status: Done
assignee:
  - '@claude'
created_date: '2026-07-29 17:36'
updated_date: '2026-07-29 18:51'
labels:
  - check
dependencies:
  - TASK-110
  - TASK-111
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Only 4 of 21 rules are active gates. Enabling all in [tool.pypeeker].rules surfaces ~215 findings and breaks 12 tests that assert certain rules are opt-in (e.g. test_not_in_default_rules). Triage from a full-repo run: ~13 rules report ZERO findings on src/ and could become gates immediately; prefer-tuple (41) and unused-imports (6) are blocked on their fix tasks; ~100 findings (no-argument-mutation, unused-return-value, no-hidden-global-mutation) have NO refactoring tool and are largely intentional patterns (the precondition-state builder) or tested-only API. Goal: turn on the safe subset, keep advisory rules opt-in by design, and record a decision for the no-tool findings (add a fix op, hand-refactor, or scope/exclude). This is the substance behind the 'use all the rules' request; it is not a config flip.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The rules that report zero findings on src/ are enabled in [tool.pypeeker].rules and 'pypeeker check' stays green (self-lint gate).
- [x] #2 Rules that remain opt-in by design stay documented as such; the 'not in default rules' tests change only for rules that genuinely move to default.
- [x] #3 A recorded decision exists for the no-tool findings (no-argument-mutation, unused-return-value, no-hidden-global-mutation): refactor, add a fix, or scope/exclude — with rationale.
- [x] #4 prefer-tuple and unused-imports are enabled as gates only after their fix tasks land (or are explicitly deferred).
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Enabled the 7 zero-default-finding hygiene rules (11 total). Deferred rules documented in review/07-rule-adoption.md with per-rule rationale. Visibility rules deferred as a group (need privatize/promote burn-down first). no-tool findings kept opt-in with rationale (AC3).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Expanded the self-lint gate from 4 to 11 rules: added the 7 builtin rules that report zero findings on src/ at default confidence (star-imports, import-time-side-effects, docstring-drift, unused-imports, pure-decorator-contracts, naming-conventions, test-only-production-code). Each rule's "not in default" test now asserts it is enabled. review/07-rule-adoption.md records the deferral decisions: the three no-tool findings (no-argument-mutation, unused-return-value, no-hidden-global-mutation) stay opt-in with rationale; prefer-tuple stays advisory (fix landed in TASK-110 but adoption is churn); no-impure-functions and born-private are inert without extra config; the four visibility rules are deferred as a group pending a privatize/promote burn-down of their 68 findings. Self-lint green, 1390 tests pass.
<!-- SECTION:FINAL_SUMMARY:END -->
