---
id: TASK-66
title: 'check: project-scoped rule context (cross-file rules)'
status: To Do
assignee: []
created_date: '2026-06-11 15:47'
labels:
  - check
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Rule = (FileIndex, options) structurally prevents any rule that needs the resolver, the tree, or a second file — yet the most valuable upcoming semantic rules (unused public symbol, impure-function policy, dead re-exports) are cross-module, and the analysis layer they need already exists. Give rules a project-scoped context with the per-file signature kept as a convenience wrapper.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Rules can access all indexes, a shared CrossModuleResolver, and the symbol tree via a project-scoped context
- [ ] #2 Existing per-file rules keep working unchanged (wrapper or adapter preserves the current Rule signature)
- [ ] #3 At least one cross-file rule or test demonstrates the new capability
- [ ] #4 register_rule plugin path supports project-scoped rules
<!-- AC:END -->
