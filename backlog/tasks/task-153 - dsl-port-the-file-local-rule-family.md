---
id: TASK-153
title: 'dsl: port the file-local rule family'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-08-03 18:11'
updated_date: '2026-08-04 02:19'
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

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v4 in /home/user/pypeeker-wt153. STAGED: prefer-tuple ported and claimed FIRST (first real exercise of the differential oracle — proof-of-life), then the rest of the file-local family, claiming each at parity. TASK-154/155 fan out in parallel worktrees after this merges.
<!-- SECTION:PLAN:END -->
