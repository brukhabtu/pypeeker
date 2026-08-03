---
id: TASK-157
title: 'dsl: the flip — gate cutover, CLI rewiring, frozen-path deletion'
status: To Do
assignee: []
created_date: '2026-08-03 18:11'
labels: []
dependencies:
  - TASK-156
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 5 of the DSL rewrite (dsl-rewrite.md is normative; test policy is migrate plus port — the program's one big-bang PR). Precondition: all 22 rules at differential parity. The self-lint gate switches to the new engine; CLI commands re-wire to named expressions; fix_id becomes purely derived; baseline identity re-keys to rule_id plus anchor_id; the frozen paths (src/pypeeker/check/**, app/check_fixes.py, app/privatize.py) are DELETED in this same PR along with the freeze guards (settings deny, bash hook, CI guard) and the differential harness; old-engine tests are ported scenario-by-scenario per the port policy; CLAUDE.md and architecture.md updated; dsl-rewrite.md flips to historical record. After this PR no package name carries a version scar.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The self-lint gate runs the new engine at zero findings with no baseline
- [ ] #2 All old-engine test scenarios have a one-for-one home or a ledger entry
- [ ] #3 Frozen paths, freeze guards, and the differential harness are deleted in the same PR
- [ ] #4 CLAUDE.md and architecture.md describe the new architecture accurately
- [ ] #5 Full gate green
<!-- AC:END -->
