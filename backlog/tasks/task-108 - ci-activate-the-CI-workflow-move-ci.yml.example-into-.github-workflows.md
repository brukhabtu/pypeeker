---
id: TASK-108
title: 'ci: activate the CI workflow (move ci.yml.example into .github/workflows)'
status: To Do
assignee: []
created_date: '2026-07-03 03:06'
labels:
  - ci
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The CI workflow ships as .github/ci.yml.example and was never activated, so tests, ruff, and the self-lint (index + check, now with import-boundaries strict + barrel-only) do not run on PRs. Move it into .github/workflows/ci.yml so enforcement actually gates changes.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The workflow lives at .github/workflows/ci.yml and runs pytest, ruff, and pypeeker index+check on push to main and on PRs
<!-- AC:END -->
