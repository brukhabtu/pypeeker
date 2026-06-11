---
id: TASK-72
title: 'Query output: include resolution confidence in refs/symbol JSON'
status: To Do
assignee: []
created_date: '2026-06-11 15:47'
labels:
  - query
  - resolve
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The capability+confidence model exists so consumers (LLMs) can calibrate trust, but refs --all output cannot distinguish a DECLARED match from one that relied on constructor-inferred receiver types. The declared_only machinery in CrossModuleResolver already knows the difference — surface it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 find_all_references / refs --all output marks each match with how it resolved (direct, import-alias, barrel, receiver-declared, receiver-inferred)
- [ ] #2 Rename's declared_only gating reuses the same classification rather than a parallel code path
- [ ] #3 JSON shape documented; tests cover at least declared vs inferred receiver matches
<!-- AC:END -->
