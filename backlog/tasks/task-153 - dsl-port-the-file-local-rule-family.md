---
id: TASK-153
title: 'dsl: port the file-local rule family'
status: To Do
assignee: []
created_date: '2026-08-03 18:11'
labels: []
dependencies:
  - TASK-152
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 3a of the DSL rewrite (dsl-rewrite.md is normative). Port the file-local registry and builtin rules to DSL expressions, claiming each in the parity manifest as it reaches differential parity on this repo plus fixtures. Divergences go in the dsl-rewrite.md ledger, never silently. The old rules are frozen spec: read them sparingly and ranged; their OUTPUT via the old CLI is the ground truth.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Each ported rule is claimed in the parity manifest and differentially identical to the old engine, or its divergence is in the ledger
- [ ] #2 Full gate green
<!-- AC:END -->
