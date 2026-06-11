---
id: TASK-100
title: 'ci: GitHub Actions workflow (tests + self-lint + ruff)'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-06-11 18:28'
updated_date: '2026-06-11 18:50'
labels:
  - ci
  - m6-ratchets
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The repo has no CI. Add a workflow: uv-installed Python 3.14, uv sync, pytest, pypeeker index+check self-lint, ruff check (add minimal [tool.ruff] config). Doubles as the reference CI story for consumer projects.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Workflow runs pytest and the pypeeker self-lint on push/PR using uv
- [ ] #2 Minimal ruff configuration added and passing over src/
- [ ] #3 Workflow YAML lints clean (actionlint if available) and is documented in README or architecture.md
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add ruff as dev dependency via uv add --dev ruff
2. Add minimal [tool.ruff] config (target-version, line-length, conservative rule set E,F,W,I,UP) and iterate until ruff check src tests passes clean
3. Create .github/workflows/ci.yml: setup-uv, uv python install 3.14, uv sync, pytest, ruff check, pypeeker self-lint
4. Document CI subsection in architecture.md
5. Validate: actionlint if available, YAML parse, run all CI steps locally
<!-- SECTION:PLAN:END -->
