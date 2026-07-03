---
id: TASK-108
title: 'ci: activate the CI workflow (move ci.yml.example into .github/workflows)'
status: To Do
assignee: []
created_date: '2026-07-03 03:06'
updated_date: '2026-07-03 03:08'
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

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Blocked: pushing .github/workflows/ci.yml is rejected by the remote — the GitHub App used for this session lacks the `workflows` permission ("refusing to allow a GitHub App to create or update workflow ... without workflows permission"). Same constraint that left the file as .example originally.

To activate, a user with workflow push rights runs from a clean checkout:
  git mv .github/ci.yml.example .github/workflows/ci.yml
  git commit -m "ci: activate workflow" && git push
Or grant the Claude GitHub App the workflows permission and re-run this task. The workflow content is ready as-is; uv python install 3.14 works on GitHub runners (only this session's egress proxy blocks it).
<!-- SECTION:NOTES:END -->
