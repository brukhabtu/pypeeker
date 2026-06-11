---
id: TASK-73
title: 'Config: single pyproject parser owned by project.py'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 15:47'
updated_date: '2026-06-11 15:53'
labels:
  - config
  - clarity
dependencies: []
priority: low
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
project.py and check/config.py both parse [tool.pypeeker] and both define a ('src',) default. One config module should own pyproject access; check should consume it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 One module owns [tool.pypeeker] parsing; check/config.py consumes it
- [x] #2 Single source of truth for the default src roots
- [x] #3 Full test suite passes
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add load_pypeeker_section(project_root) to project.py returning the raw [tool.pypeeker] dict ({} when file/section absent); rebuild load_src_roots on it
2. Rewrite check/config.py load_config to consume project.load_pypeeker_section; make DEFAULT_SRC an alias of project.DEFAULT_SRC_ROOTS (keep CheckConfig dataclass and load_config signature intact)
3. Add "project" to the check allow list in pyproject.toml [tool.pypeeker.import-boundaries.allow] so the new import passes the boundary rule
4. Run uv run pytest -q and the pypeeker check self-lint
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Added load_pypeeker_section(project_root) to src/pypeeker/project.py as the single owner of [tool.pypeeker] access (returns {} when file/section absent); rebuilt load_src_roots on top of it
- Rewrote check/config.py load_config to consume project.load_pypeeker_section; removed its tomllib import; DEFAULT_SRC now aliases project.DEFAULT_SRC_ROOTS (CheckConfig dataclass and load_config signature unchanged)
- Added "project" to the check allow list in pyproject.toml [tool.pypeeker.import-boundaries.allow] so the new import passes the boundary rule
- New tests/test_project.py covers load_pypeeker_section and load_src_roots; tests/test_check_config.py asserts DEFAULT_SRC is DEFAULT_SRC_ROOTS
- Verified: uv run pytest -q -> 495 passed, 10 skipped; uv run pypeeker check -> exit 0 (no violations)
- Note: indexer.py/applier.py/architecture.md diffs in the tree belong to other agents; untouched by this task
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Made pypeeker.project the single owner of [tool.pypeeker] pyproject parsing, eliminating the duplicated TOML parser and ("src",) default in check/config.py.

Changes:
- src/pypeeker/project.py: new load_pypeeker_section(project_root) returns the raw [tool.pypeeker] dict ({} when pyproject.toml or the section is absent/malformed); load_src_roots is now a thin wrapper over it.
- src/pypeeker/check/config.py: load_config consumes project.load_pypeeker_section instead of re-opening the TOML; DEFAULT_SRC is now an alias of project.DEFAULT_SRC_ROOTS so there is exactly one default. CheckConfig dataclass and the load_config(project_root) -> CheckConfig public signature are unchanged, so cli.py and existing callers are unaffected.
- pyproject.toml: added "project" to the check entry in [tool.pypeeker.import-boundaries.allow] (project is a leaf package, so the layering stays sound).

Tests:
- New tests/test_project.py for load_pypeeker_section/load_src_roots (missing file, missing section, raw table passthrough, defaults, configured values).
- tests/test_check_config.py asserts DEFAULT_SRC is DEFAULT_SRC_ROOTS.
- uv run pytest -q: 495 passed, 10 skipped. uv run pypeeker check: clean (exit 0), confirming the new check -> project import passes the boundary rule.
<!-- SECTION:FINAL_SUMMARY:END -->
