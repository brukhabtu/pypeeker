---
id: DRAFT-1
title: plan-inline-variable
status: Draft
assignee: []
created_date: '2026-05-25 13:02'
labels: []
dependencies:
  - TASK-51
  - TASK-52
  - TASK-53
priority: low
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Inline a local variable: replace each reference of the variable with its assigned value expression and delete the assignment. Requires: a single assignment (or last-wins), the value expression is pure (no side effects / re-evaluation hazard), and all references found (query). Built on transaction INSERT/DELETE + CST + range data-flow/purity.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A single-assignment local with a pure value expression is inlined at all references and its assignment removed
- [ ] #2 Refuses to inline when the value is impure or the variable is reassigned/escapes; tests cover safe + refused cases
<!-- AC:END -->
