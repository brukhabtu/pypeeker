---
id: TASK-112
title: 'check: adopt more rules as gates + decide policy for advisory/no-tool findings'
status: To Do
assignee: []
created_date: '2026-07-29 17:36'
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
- [ ] #1 The rules that report zero findings on src/ are enabled in [tool.pypeeker].rules and 'pypeeker check' stays green (self-lint gate).
- [ ] #2 Rules that remain opt-in by design stay documented as such; the 'not in default rules' tests change only for rules that genuinely move to default.
- [ ] #3 A recorded decision exists for the no-tool findings (no-argument-mutation, unused-return-value, no-hidden-global-mutation): refactor, add a fix, or scope/exclude — with rationale.
- [ ] #4 prefer-tuple and unused-imports are enabled as gates only after their fix tasks land (or are explicitly deferred).
<!-- AC:END -->
