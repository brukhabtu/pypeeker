---
id: TASK-162
title: >-
  post-flip residue: shared fix pass, treebuild exemption, physical registry
  split
status: To Do
assignee: []
created_date: '2026-08-08 19:59'
updated_date: '2026-09-08 02:31'
labels:
  - dsl
  - cleanup
dependencies:
  - TASK-163
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The post-flip residue TASK-157 deliberately left open and ledgered (dsl-rewrite.md, flip entries), now that the sanctioned-duplication rationale has expired. The coercion-helper half of the original scope moved to TASK-163. What remains here: (1) app/fix_run.py carries two copies of one planning pass (single-pass and fixpoint); lift the shared pass into one home (app/intent_fixes.py or fix_run.py) with byte-identical check --fix output; (2) the treebuild.build_tree over-exposed-module-symbol allowance in pyproject.toml is an exemption with the decision deliberately left open — decide (privatize, or keep public with a stated consumer) and remove the allowance; (3) the rule-engine framework/library split in dsl/rules.py is logical, not physical (register_dsl_rule and dsl_rule share a module with RULES, which imports every rule module) — move the rule types and the registry into a rule-free dsl/registry.py so the framework is importable without the library, as architecture.md now promises as the follow-up.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 check --fix and check --fix --plan output is byte-identical before and after the planning pass is shared; one implementation remains
- [ ] #2 The treebuild.build_tree self-lint allowance is removed with the decision recorded in architecture.md
- [ ] #3 dsl/registry.py holds the rule types and registry with no import of any rule module; architecture.md's follow-up note is discharged
- [ ] #4 Full gate green
<!-- AC:END -->
