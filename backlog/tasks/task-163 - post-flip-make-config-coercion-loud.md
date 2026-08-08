---
id: TASK-163
title: 'post-flip: make config coercion loud'
status: To Do
assignee: []
created_date: '2026-08-08 19:59'
labels:
  - dsl
  - cleanup
dependencies:
  - TASK-157
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Quirks the ports reproduce byte-for-byte for oracle fidelity, to be fixed once the frozen engine is gone (each with a ledger entry, since findings change): (1) the injected [tool.pypeeker.visibility] table empties option enum sets via silent _as_enum_set drops, making require-docstrings completely dead on any project declaring that table — a real bug; (2) rules = "prefer-tuple" (bare string) silently becomes a tuple of characters so no rule matches; (3) silent drops of unparseable enum option values generally. Replace with loud structured refusals per the DSL's own philosophy.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The visibility-injection no longer silences rules; a bare-string rules value and unparseable enum options refuse loudly
- [ ] #2 Ledger entries record each behavior change
- [ ] #3 Full gate green
<!-- AC:END -->
