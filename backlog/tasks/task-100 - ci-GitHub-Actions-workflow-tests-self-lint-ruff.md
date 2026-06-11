---
id: TASK-100
title: 'ci: GitHub Actions workflow (tests + self-lint + ruff)'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 18:28'
updated_date: '2026-06-11 18:56'
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
- [x] #1 Workflow runs pytest and the pypeeker self-lint on push/PR using uv
- [x] #2 Minimal ruff configuration added and passing over src/
- [x] #3 Workflow YAML lints clean (actionlint if available) and is documented in README or architecture.md
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add ruff as dev dependency via uv add --dev ruff
2. Add minimal [tool.ruff] config (target-version, line-length, conservative rule set E,F,W,I,UP) and iterate until ruff check src tests passes clean
3. Create .github/workflows/ci.yml: setup-uv, uv python install 3.14, uv sync, pytest, ruff check, pypeeker self-lint
4. Document CI subsection in architecture.md
5. Validate: actionlint if available, YAML parse, run all CI steps locally
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- ruff was already a dev dependency (ruff>=0.15.17 in [tool.uv].dev-dependencies, locked); confirmed with uv lock --check
- Added [tool.ruff] to pyproject.toml: target-version py314 (accepted by ruff 0.15.17), line-length 100, select E,F,W, ignore E501 (existing lines up to 204 chars), tests/fixtures excluded (intentionally broken code), targeted per-file-ignores for E402 (check/builtin/__init__.py registration order), F841 (test_binder, test_planner), E702 (purity tests with deliberate semicolons)
- Excluded rule I (import sorting): 17 files would need --fix mid-wave while other agents edit them; noted as follow-up. UP also not clean (20 findings) so left out
- Created .github/workflows/ci.yml: push to main + pull_request, single ubuntu-latest job, astral-sh/setup-uv@v5 with enable-cache, uv python install 3.14, uv sync, pytest -q, ruff check src tests, pypeeker index src + pypeeker check

- Local validation: uv sync OK; uv run pytest -q -> 771 passed; uv run ruff check src tests -> All checks passed; uv run pypeeker index src && uv run pypeeker check -> exit 0
- actionlint not available: uvx actionlint fails (Go tool, not on PyPI) and sandbox blocks downloading the release binary; validated YAML instead via python3 -c yaml.safe_load -> parses clean
- Documented CI subsection in architecture.md (before References) as the reference CI story for consumer projects
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added GitHub Actions CI and a minimal ruff configuration; documented the workflow in architecture.md as the reference CI story for consumer projects.

Changes:
- .github/workflows/ci.yml: on push to main + pull_request; single ubuntu-latest job using astral-sh/setup-uv@v5 (built-in cache), uv python install 3.14, uv sync, uv run pytest -q, uv run ruff check src tests, and the self-lint (uv run pypeeker index src && uv run pypeeker check).
- pyproject.toml: [tool.ruff] with target-version py314, line-length 100, select E,F,W (E501 ignored due to pre-existing long lines), tests/fixtures excluded, and targeted per-file-ignores (E402 in check/builtin/__init__.py, F841 in test_binder/test_planner, E702 in purity tests). ruff>=0.15.17 confirmed as dev dependency in [tool.uv].dev-dependencies.
- architecture.md: new CI section describing the workflow and the index+check pair as the consumer-project integration pattern.

Tests:
- uv run pytest -q: 771 passed
- uv run ruff check src tests: clean
- pypeeker self-lint: exit 0; workflow YAML parses clean (actionlint unavailable in this environment)

Follow-ups:
- Enable ruff rule I (import sorting, 17 files) and consider UP (20 findings) once the codebase is quiet; revisit E501 with an agreed line limit.
<!-- SECTION:FINAL_SUMMARY:END -->
