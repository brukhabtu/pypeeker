---
id: TASK-96
title: 'cli: promote/demote — visibility changes as planned transactions'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 18:28'
updated_date: '2026-06-11 20:55'
labels:
  - cli
  - visibility
  - m5-visibility
dependencies:
  - TASK-94
  - TASK-95
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The minimal-visibility workflow needs first-class operations: demote SYMBOL plans name->_name incl. all references, barrel/__all__ updates (alias options per existing rename flags); promote plans _name->name plus export addition. Both refuse when hierarchy facts show an override contract or library-mode public roots forbid it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 pypeeker demote/promote SYMBOL_ID produce previewable transactions reusing the rename engine, incl. export handling
- [x] #2 Hierarchy-unsafe and public-root-protected operations are refused with structured errors
- [x] #3 CLI tests cover both directions, export updates, and refusals
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Read RenamePlanner, hierarchy, VisibilityConfig, resolver barrel facts (done)
2. New refactor/visibility_ops.py: VisibilityPlanner with plan_demote/plan_promote orchestrating RenamePlanner; DemoteError/PromoteError structured refusals (library-mode public roots, underscore shape); export handling via include_exports/keep_export; add_export INSERT edits into package __init__; persist operation demote/promote by re-saving the header through TransactionStore
3. cli.py: demote/promote commands with freshness pattern, JSON summary or {"error"}+exit 1
4. tests/test_promote_demote.py: planner + CliRunner tests on tmp projects covering renames incl. barrel updates, refusals (override, library mode, collision, dunder), keep_export alias, add_export import+__all__, apply round-trip
5. uv run pytest -q clean, ruff clean
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Added src/pypeeker/refactor/visibility_ops.py: VisibilityPlanner.plan_demote/plan_promote as thin orchestration over RenamePlanner; DemoteError/PromoteError carry a stable refusal code (already-private, already-public, dunder, protected-public-api, export-target, rename-refused, not-found, ambiguous).
- Demote: refuses underscore-prefixed names and library-mode public-root-protected barrel exports (mirrors check.rules._public_root_protected, scoped to one symbol); barrel-exported symbols plan with include_exports=True plus a warning that consumers were rewritten; keep_export maps to the planner keep-export aliasing flag.
- Promote: strips one leading underscore (dunder refused); existing barrel re-exports of the private name are rewritten via include_exports with a warning; --add-export PKG appends INSERT EditEntries to the same transaction: import line after the last column-0 import (top of file when none, documented limit), plus a name prepended right after the __all__ opening bracket (sidesteps trailing-comma handling). Export-target validation runs BEFORE planning so a refused add_export leaves no transaction behind.
- Header operation: no cosmetic gap needed — TransactionHeader already has an operation field defaulting to "rename"; _finalize reloads the saved transaction, sets header.operation to demote/promote, appends extra edits, and re-saves through TransactionStore (the update_status header-rewrite pattern). apply/rollback/transactions show work unchanged; method-override-safe and scope-conflict refusals propagate from the planner with their original messages.
- cli.py: pypeeker demote SYMBOL_ID [--keep-export] and pypeeker promote SYMBOL_ID [--add-export PKG] with the _refresh_index/--no-refresh freshness pattern; JSON summary (plus warnings) or {"error","code"} + exit 1; help text documents the refusal classes.
- tests/test_promote_demote.py: 22 tests (CliRunner + direct planner) — demote renames def+barrel+consumer with apply/rollback round-trip content-verified; keep-export aliasing; refusals for override method, library mode (and allowed outside explicit public-roots), _name collision, already-private, dunder, already-public, unknown/conflicting export target; add-export with and without __all__; header operation persisted.
- uv run pytest -q: 1120 passed; ruff clean.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added first-class visibility operations: pypeeker demote (name -> _name) and pypeeker promote (_name -> name) as planned transactions reusing the rename engine.

Changes:
- New src/pypeeker/refactor/visibility_ops.py: VisibilityPlanner orchestrates RenamePlanner so every reference, import, and barrel re-export is rewritten through the existing engine and the result is an ordinary pending transaction (apply/rollback/preview unchanged). The transaction header operation field is rewritten to demote/promote by reloading and re-saving through TransactionStore.
- Structured refusals (DemoteError/PromoteError with stable codes): underscore-shape violations, dunder names, library-mode public-root-protected barrel exports ("protected public API (library mode)"), invalid export targets, and propagated rename preconditions (scope name conflict, method-override-safe hierarchy check — allow_override_rename is never passed).
- Export handling: barrel-exported symbols plan with include_exports and warn that consumers were rewritten; demote --keep-export aliases the re-export (from .mod import _name as name) to hold the public surface; promote --add-export PKG appends INSERT edits adding from .mod import Name to PKG/__init__.py (after the last top-level import) and prepends the name to __all__ when present — validated before planning so refusals leave no transaction behind.
- cli.py: demote/promote commands with the standard freshness pattern, JSON summaries (+warnings) or {"error","code"} with exit 1, refusal classes documented in help.

Tests:
- tests/test_promote_demote.py (22 tests): both directions end-to-end via CliRunner incl. apply/rollback round-trip with byte-for-byte content verification, barrel/consumer rewrites, keep-export aliasing, add-export with/without __all__, and every refusal class; plus direct planner tests.
- Full suite: 1120 passed; ruff clean.

Limits/risks: add-export insertion is line-based (no-import files get the line above any docstring; __all__ must be a literal list/tuple) — documented in docstrings. Override-refusal messages mention allow_override_rename, which applies to plan-rename, not demote/promote.
<!-- SECTION:FINAL_SUMMARY:END -->
