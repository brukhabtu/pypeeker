---
id: TASK-103
title: 'check: curate models barrel and migrate consumers off deep imports'
status: To Do
assignee: []
created_date: '2026-07-03 01:57'
labels:
  - architecture
  - refactor
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The models package __init__ is an empty barrel, so ~150 in-repo sites deep-import internal modules (from pypeeker.models.symbols import ...). models has no declared public surface. Curate it like the storage/check/query/refactor barrels and migrate src/ consumers to import via the barrel. Deferred during the architecture review due to blast radius; do it as its own change.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 models/__init__.py re-exports the public surface with an explicit __all__, matching the style of storage/__init__.py
- [ ] #2 All src/pypeeker consumers outside models/ import model types via 'from pypeeker.models import X' (tests may keep deep imports)
- [ ] #3 uv run pytest, uv run ruff check src tests, and uv run pypeeker index src && uv run pypeeker check all pass
<!-- AC:END -->
