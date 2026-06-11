---
id: TASK-60
title: 'Freshness: ensure indexes are fresh before query/check/refactor commands'
status: To Do
assignee: []
created_date: '2026-06-11 15:46'
labels:
  - cli
  - storage
dependencies: []
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
storage-transaction-architecture.md promises 'before any operation, hash source files and re-index stale files', but only RenamePlanner._validate_files checks staleness. check, symbol, refs, scope, tree, and the extract/inline planners silently serve stale data; extract/inline are worst since they read current file bytes while trusting a stale index for scopes/references. Add a single ensure-fresh step (reuse index_path, which already skips unchanged files) applied by the CLI before serving queries, with an opt-out flag.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Query/check CLI commands (symbol, refs, scope, tree, check) re-index stale files before answering, or refuse with a clear staleness error
- [ ] #2 extract-variable, extract-method, and inline-variable planners refuse or refresh stale indexes before planning
- [ ] #3 An opt-out flag (e.g. --no-refresh) preserves the old fast path
- [ ] #4 Behavior documented (README/architecture or command help)
<!-- AC:END -->
