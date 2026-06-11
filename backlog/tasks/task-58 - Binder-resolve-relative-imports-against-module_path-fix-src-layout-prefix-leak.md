---
id: TASK-58
title: >-
  Binder: resolve relative imports against module_path (fix src-layout prefix
  leak)
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 15:46'
updated_date: '2026-06-11 15:57'
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
- [x] #1 Relative imports in src-layout files produce imported_from rooted at the dotted module path (no src. prefix)
- [x] #2 CrossModuleResolver resolves relative-import consumers (incl. __init__ barrel re-exports written with relative imports) to canonical definitions; rename reaches them
- [x] #3 import-boundaries flags violations introduced via relative imports
- [x] #4 Indexing pypeeker's own src yields no imported_from values starting with 'src.'
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Change resolve_relative_import in src/pypeeker/binder/helpers.py to resolve against the dotted module_path instead of the physical file path: signature (module_path, module_name, *, is_package=False); level-1 strips one segment for regular modules, zero for packages (__init__.py), each extra dot strips one more.
2. Update call site in src/pypeeker/binder/imports.py: compute is_package from state.file_path basename == __init__.py and pass state.module_path.
3. Tests: binder unit tests for src-layout relative imports (regular module, __init__ barrel, multi-level .., from . import x, no src. prefix); resolver tests for relative-import consumers and relative barrel re-export chains incl. find_all_references; check-rules test that import-boundaries flags a violation introduced via a relative import in a src-layout file; end-to-end test via indexer.index_path over a src/ tree asserting no imported_from starts with src.
4. Run uv run pytest -q (baseline 486 passed, 10 skipped) and reindex pypeeker own src in a scratch copy to verify AC#4.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Rewrote resolve_relative_import (binder/helpers.py) to resolve against the dotted module_path instead of the physical file path; new signature (module_path, module_name, *, is_package=False). For __init__.py files module_path already names the package, so level-1 strips zero segments vs one for regular modules; removed now-unused pathlib import.
- Updated call site in binder/imports.py: computes is_package from state.file_path basename and passes state.module_path.
- Added TestRelativeImports (8 tests) in tests/test_binder.py incl. end-to-end index_path test asserting no src.-prefixed imported_from.
- Added 4 resolver tests in tests/test_resolve.py (relative import to definition, relative barrel chain, find_all_references via relative imports, multi-level ..).
- Added 2 import-boundaries tests in tests/test_check_rules.py (relative import violation flagged; permitted relative import passes).
- Verified AC#4 by reindexing pypeeker src in a scratch copy: 54 files, 0 errors, 0 src.-prefixed imported_from, 324 pypeeker.* values; models/index.py relative imports resolve to pypeeker.models.references.Reference etc.
- Full suite: 523 passed, 10 skipped, 1 failed (tests/test_cli_freshness.py::test_check_runs_against_refreshed_index) — unrelated: no import statements involved; exercises CLI check freshness/config files concurrently edited by other agents (cli.py, indexer.py, check/config.py, project.py).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fixed the src-layout prefix leak in relative-import resolution: the binder now resolves relative imports against the dotted, src-stripped module_path instead of the physical file path.

Problem:
resolve_relative_import built targets from the file path, so `from .references import X` in src/pypeeker/models/index.py produced imported_from=`src.pypeeker.models.references` while indexed modules are src-stripped (`pypeeker.models.references`). CrossModuleResolver then treated every relative-import consumer as external (rename/find-all-references silently missed them) and the import-boundaries rule exempted relative imports from layer enforcement.

Changes:
- src/pypeeker/binder/helpers.py: resolve_relative_import now takes (module_path, module_name, *, is_package=False) and stays pure. For a regular module `pkg.sub.mod`, `.x` resolves against `pkg.sub`; each extra dot climbs one package. For a package `__init__.py`, module_path already names the package (`pkg.sub`), so one fewer segment is stripped (`.x` -> `pkg.sub.x`). Over-deep relative imports degrade to the bare name as before.
- src/pypeeker/binder/imports.py: call site passes state.module_path and is_package derived from the file basename (`__init__.py`).

Tests (14 new):
- tests/test_binder.py TestRelativeImports: src-layout module, __init__ barrel, multi-level `..` (module and __init__), `from . import sibling`, flat-layout regression, beyond-root degradation, and an end-to-end index_path test over a src/ tree asserting no `src.`-prefixed imported_from.
- tests/test_resolve.py: relative import resolves to canonical definition; relative __init__ barrel re-export chain resolves through to pkg.lib:Widget; find_all_references reaches relative-import consumers; multi-level relative import.
- tests/test_check_rules.py: import-boundaries flags a forbidden `from ..storage import ...` in a src-layout file and passes a permitted one.

Verification:
- Targeted suites green (148 passed). Full suite: 523 passed, 10 skipped, 1 unrelated failure in tests/test_cli_freshness.py caused by concurrent work on check/config freshness (no imports involved in that test).
- Reindexed pypeeker's own src in a scratch copy: 54 files, 0 errors, zero imported_from starting with `src.`; models/index.py relative imports resolve to `pypeeker.models.references.Reference` etc.

Risks: none expected for flat layouts — with no src prefix the old and new resolution agree (covered by regression test).
<!-- SECTION:FINAL_SUMMARY:END -->
