---
id: TASK-58
title: >-
  Binder: resolve relative imports against module_path (fix src-layout prefix
  leak)
status: To Do
assignee: []
created_date: '2026-06-11 15:46'
labels:
  - binder
dependencies: []
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
resolve_relative_import (binder/helpers.py) builds the target module from the physical file path, so in src-layout projects 'from .references import X' in src/pypeeker/models/index.py yields imported_from='src.pypeeker.models.references' while indexed modules are src-stripped ('pypeeker.models.references'). CrossModuleResolver then treats every relative-import consumer as external (rename/find-all-references silently miss them) and the import-boundaries rule exempts relative imports from layer enforcement. The binder already has state.module_path; relative imports must resolve against it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Relative imports in src-layout files produce imported_from rooted at the dotted module path (no src. prefix)
- [ ] #2 CrossModuleResolver resolves relative-import consumers (incl. __init__ barrel re-exports written with relative imports) to canonical definitions; rename reaches them
- [ ] #3 import-boundaries flags violations introduced via relative imports
- [ ] #4 Indexing pypeeker's own src yields no imported_from values starting with 'src.'
<!-- AC:END -->
