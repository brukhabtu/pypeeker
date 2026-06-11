---
id: TASK-79
title: 'check: test-only-production-code rule (project-scoped)'
status: To Do
assignee: []
created_date: '2026-06-11 18:26'
labels:
  - check
  - m1-advisory
dependencies:
  - TASK-74
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
A production symbol whose only references come from test paths should not be public production API (and may be dead). Requires cross-module reference truth: resolver + path classification. Builds on the CheckContext from task-66.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Rule flags module-level production symbols all of whose project references originate from configured test globs (default tests/)
- [ ] #2 Zero-reference symbols are excluded (that is unused-public-symbol's job); barrel re-exports excluded
- [ ] #3 Options: test path globs, allow patterns; opt-in; tests cover test-only, prod-used, and mixed usage
<!-- AC:END -->
