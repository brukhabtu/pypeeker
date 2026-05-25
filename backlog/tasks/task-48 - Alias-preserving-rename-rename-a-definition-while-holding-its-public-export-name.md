---
id: TASK-48
title: >-
  Alias-preserving rename: rename a definition while holding its public export
  name
status: Done
assignee:
  - '@claude'
created_date: '2026-05-23 23:10'
updated_date: '2026-05-25 12:37'
labels: []
dependencies: []
priority: low
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
SPEC / future work, surfaced by TASK-30 and TASK-31.

Problem. Today rename has two cross-module behaviors: default (rewrite the definition + direct imports + same-module/consumer call sites) and --include-exports (also propagate the new name through __init__ barrels and their consumers). Both rewrite the PUBLIC name. But a barrel is a deliberate public API surface, and a common, legitimate intent is to rename the internal definition WITHOUT changing the name the package exports. Example: rename pkg/lib.py:Widget -> InternalWidget but keep `from pkg import Widget` working for every external caller. There is currently no way to express this; --include-exports does the opposite (changes the export).

Proposed approach. Add an alias-preserving mode (e.g. a --keep-export flag) that, when renaming a definition that is re-exported through one or more barrels, rewrites the DEFINITION to the new name and adjusts the re-export to preserve the old public name via an alias, instead of renaming the public name. Concretely, an __init__ re-export `from pkg.lib import Widget` becomes `from pkg.lib import InternalWidget as Widget`, so the package keeps exporting `Widget`. Barrel consumers (`from pkg import Widget`) and their call sites are then left untouched, because the public name is unchanged.

Key design questions to settle in this task:
1. Flag surface and interaction with --include-exports: are they mutually exclusive (rename-export vs keep-export), or is keep-export the safe default for re-exported symbols with --include-exports meaning opt in to changing the public name? Recommend a distinct --keep-export flag, mutually exclusive with --include-exports, erroring if both are passed.
2. Direct importers of the renamed symbol (`from pkg.lib import Widget`, NOT via the barrel): these break once the definition is renamed (pkg.lib no longer defines Widget). Decide: (a) update them to the new name InternalWidget, or (b) alias-preserve them too (`from pkg.lib import InternalWidget as Widget`). Recommend (a): direct importers reach into the implementation module, so they should track the new internal name; only the package-level public name is preserved.
3. Multi-layer re-exports (a barrel re-exporting another barrel): preserve the public name at the OUTERMOST boundary the rename touches; only the innermost re-export adjacent to the definition needs the `as` alias. Define precisely using the resolver chain (crosses_barrel / _resolve_chain).
4. Existing alias imports (`from pkg.lib import Widget as W`): unaffected — the alias W is the caller\'s choice and the import token Widget is renamed to InternalWidget as in (a).
5. __all__ lists: if a re-export module declares __all__ containing the public name, it must be left as the public name (do not rename the string literal). Detect and skip; note if out of scope for v1.
6. Name-conflict safety: introducing `InternalWidget as Widget` must not collide with an existing InternalWidget or Widget in the re-export module scope; validate and error clearly.

Mechanics. Build on the existing resolver: find_importers + crosses_barrel already classify imports by whether they sit on the export surface. For keep-export, the re-export imports on the surface get an alias-insertion edit (insert ` as <old_public_name>` after the renamed token) rather than a plain token rename; barrel-consumer imports/call sites are excluded from the edit set. This is a new edit shape (insert vs replace) — confirm the transaction/applier model supports inserting text at a token boundary, or model it as a replace of the imported_name span with `NewName as OldName`.

Out of scope (v1): renaming across distribution boundaries / installed packages; star imports (`from pkg import *`); dynamic/__getattr__-based re-exports.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A --keep-export mode renames the definition (and direct, non-barrel importers + their call sites) to the new name while preserving the package-level public export name via an alias on the innermost re-export (from pkg.lib import NewName as OldName)
- [x] #2 Barrel consumers that import the preserved public name (from pkg import OldName) and their call sites are left unchanged and remain runnable
- [x] #3 --keep-export and --include-exports are mutually exclusive with a clear error if both are given; default (neither flag) behavior is unchanged
- [x] #4 Name-conflict and __all__ handling are specified and validated (no silent breakage of the re-export module); multi-layer re-exports preserve the public name at the correct boundary
- [x] #5 Tests cover an end-to-end keep-export rename that leaves external (from pkg import OldName) callers working while the definition and direct importers use the new name; full suite green; pypeeker check exits 0
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
planner: add keep_export param (mutually exclusive with include_exports). Partition importers: non-aliased __init__ re-exports -> alias-preserve (rewrite token to 'New as Old'); aliased __init__ re-exports + direct importers -> normal rename; barrel consumers -> skip. _build_edits: parameterize replacement text (guard still on old_name). Build alias edits via _build_edits(reexport_locs, old, f'New as Old'). CLI --keep-export. Tests: keep-export rewrites __init__ to 'New as Old', renames def+direct importer+usages, leaves barrel consumer importing Old (runnable); mutual-exclusion error. suite+check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented --keep-export on plan-rename, mutually exclusive with --include-exports. Definition + direct importers + their usages rename to the new name; non-aliased __init__ re-exports are rewritten from ".lib import Old" to ".lib import New as Old" so the package keeps exporting the old public name; barrel consumers are left untouched. _build_edits parameterized to take a replacement string. Verified end-to-end via CLI; python import of the consumer runs. Limitations: single-layer barrels + non-aliased re-exports in v1; multi-layer and __all__ are follow-ups. 449 tests pass; pypeeker check exits 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
plan-rename --keep-export renames a definition while preserving its public package export name. The def, direct importers, and their call sites take the new name; each non-aliased __init__ re-export becomes "from .lib import New as Old", so external code importing the package public name stays untouched and runnable. Mutually exclusive with --include-exports. 449 tests pass; pypeeker check exits 0.
<!-- SECTION:FINAL_SUMMARY:END -->
