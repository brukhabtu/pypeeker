---
id: TASK-21
title: 'Binder: treat Python builtins as resolved references'
status: To Do
assignee: []
created_date: '2026-05-12 12:51'
updated_date: '2026-05-12 12:54'
labels:
  - binder
  - linter
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
When pypeeker indexes Python source, references to builtins (frozenset, property, len, list, ValueError, ...) are marked resolved=False. This produces hundreds of false positives for any reference-resolution downstream (rename safety, no-unresolved-refs linting). The binder should resolve builtin names to a synthetic 'builtins' module, similar to how 'from __future__ import annotations' should not produce an unresolved reference for 'annotations'.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 from __future__ import annotations does not produce an unresolved reference for 'annotations'
- [ ] #2 Tests cover at least: builtin function call (len), builtin type reference (list[int]), builtin exception in raise/except (raise ValueError), property decorator (@property), and __future__ annotations import
- [ ] #3 Re-indexing pypeeker's own source and grepping for resolved=false produces no hits on plain builtin names
- [ ] #4 Builtin names are introspected from the running interpreter via dir(builtins), filtered to drop dunder-prefixed names; the result is a frozenset built once at module load. No hardcoded list of builtin names anywhere in the binder.
- [ ] #5 Any reference whose name appears in that introspected set is marked resolved=True in the index (e.g. len, list, dict, frozenset, property, ValueError, TypeError, OSError, True, False, None, NotImplemented).
<!-- AC:END -->
