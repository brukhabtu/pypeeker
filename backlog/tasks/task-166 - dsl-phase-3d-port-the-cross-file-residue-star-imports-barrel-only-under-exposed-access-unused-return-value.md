---
id: TASK-166
title: >-
  dsl phase 3d: port the cross-file residue (star-imports, barrel-only,
  under-exposed-access, unused-return-value)
status: To Do
assignee: []
created_date: '2026-08-09 02:33'
labels:
  - dsl
dependencies:
  - TASK-158
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The four unclaimed rules the TASK-158 ownership record marked expressible with today's surface (projected sets + fact_source sweeps): star-imports (project-scoped — it consults every module's indexes, correcting TASK-153's file-local classification), barrel-only (NOT previously ported — dsl/visibility.py's BARREL_EXPORTS is only the exemption set; the rule flags cross-package deep imports resolved through re-export chains), under-exposed-access (private reach-ins from other modules), unused-return-value (resolver-driven result_used analysis). Claim each only at genuine differential parity; fixture targets with real violations per the TASK-158 pattern; divergences to the ledger.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Each of the four rules is claimed at parity or carries a ledger divergence
- [ ] #2 Each grades nonzero on some target or carries a manifest comment naming its pinning tests
- [ ] #3 Full gate green
<!-- AC:END -->
