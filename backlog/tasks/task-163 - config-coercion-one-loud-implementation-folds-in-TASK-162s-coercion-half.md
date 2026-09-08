---
id: TASK-163
title: 'config coercion: one loud implementation (folds in TASK-162''s coercion half)'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-08-08 19:59'
updated_date: '2026-09-08 02:31'
labels:
  - dsl
  - cleanup
dependencies:
  - TASK-157
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Config-option coercion in the DSL is silent where it should be loud, and duplicated where it should be one implementation. Reproduced on main after the flip (2026-09-07): a project with rules=["require-docstrings"] gets 3 findings; adding an empty [tool.pypeeker.visibility] table drops that to 0 with exit 0, because the injected visibility table empties the rule's enum option set through a silent _enum_set drop. Any consumer declaring that table has require-docstrings silently disabled. A bare-string rules="prefer-tuple" now refuses (the flip made unknown rule names loud) but with a misleading message about unknown expression 'r' — the string is iterated as characters before the refusal. Unparseable enum option values are dropped silently in general.

The same coercion is written five times: dsl/config.py as_str_list, dsl/visibility.py _as_str_list and _selected_kinds, dsl/rules.py _enum_set, dsl/sweeps.py _naming_kinds, beside project.py _as_str_tuple. These were sanctioned copies while the differential oracle was live (the new side could not execute old-engine code); that rationale expired with TASK-157, so TASK-162's coercion half is folded in here: one implementation, in project/ (or a config leaf dsl may import), reached through barrels, and loud. Each finding change gets a divergence-ledger entry in dsl-rewrite.md.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A project declaring [tool.pypeeker.visibility] gets the same require-docstrings findings as one without it (the reproduced 3-vs-0 case is pinned by a test)
- [ ] #2 A bare-string rules value and an unparseable enum option value refuse with a structured message naming the option and the accepted values, never a character-split or a silent drop
- [ ] #3 String-list and enum-set coercion has exactly one implementation, imported through barrels; the five dsl-local copies are gone
- [ ] #4 Every finding change is recorded in dsl-rewrite.md's divergence ledger
- [ ] #5 Full gate green
<!-- AC:END -->
