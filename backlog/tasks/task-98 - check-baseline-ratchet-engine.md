---
id: TASK-98
title: 'check: baseline/ratchet engine'
status: To Do
assignee: []
created_date: '2026-06-11 18:28'
labels:
  - check
  - m6-ratchets
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Adopting any rule on a legacy codebase requires 'no NEW violations': record a baseline (violation identity robust to line drift — rule + symbol/file anchor), report only deltas, update baseline explicitly. Hash-aware index makes incremental baselining natural.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 check --baseline write/compare workflow: baseline file records current violations; subsequent runs fail only on new ones; --update-baseline refreshes
- [ ] #2 Violation identity survives unrelated edits (line shifts) via symbol/file anchoring; removals shrink the baseline on update
- [ ] #3 Tests cover new-violation detection, line-drift stability, and baseline update
<!-- AC:END -->
