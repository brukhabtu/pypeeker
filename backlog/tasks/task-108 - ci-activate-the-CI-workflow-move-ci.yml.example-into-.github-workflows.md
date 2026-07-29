---
id: TASK-108
title: 'ci: activate the CI workflow (move ci.yml.example into .github/workflows)'
status: Done
assignee:
  - '@claude'
created_date: '2026-07-03 03:06'
updated_date: '2026-07-29 18:54'
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
The workflows push permission that blocked the original authoring session is available now, so git mv succeeded and pushed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Activated CI by moving .github/ci.yml.example to .github/workflows/ci.yml. CI now runs pytest, ruff check, and the pypeeker self-lint (11 rules) on pushes to main and on pull requests. Updated the CLAUDE.md and architecture.md notes that described CI as not-yet-active.
<!-- SECTION:FINAL_SUMMARY:END -->
