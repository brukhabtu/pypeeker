---
id: TASK-59
title: 'models: symbol_id module owning the id grammar and sentinel prefixes'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 15:46'
updated_date: '2026-06-11 16:07'
labels:
  - models
  - clarity
dependencies: []
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The symbol-id grammar (module.path:Scope.Chain:local$N) and the sentinel prefixes <builtins>. / <unresolved>. are the system's core abstraction but are built in binder/scope_stack+helpers and re-parsed by ad-hoc string surgery in resolve.py, query/engine.py, analysis/calls.py, analysis/writes.py, check/rules.py, and refactor/extract.py. Reference.symbol_id is overloaded across four meanings (resolved id, bare unresolved name, builtin, unresolved attribute) and every consumer re-derives the case. Centralize the grammar in one models module.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A models-level symbol-id module exposes the prefix constants and helpers (at minimum: module_of, leaf_name, is_builtin, is_unresolved_attr, shadow-suffix handling)
- [x] #2 resolve, query.engine, analysis.calls, analysis.writes, check.rules, refactor.extract, and binder.helpers consume the shared module; locally duplicated UNRESOLVED_PREFIX/BUILTINS_PREFIX constants are removed
- [x] #3 No behavioral change: full test suite passes without test edits (except imports/names)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Create src/pypeeker/models/symbol_id.py (leaf module, no pypeeker imports outside models) exposing BUILTINS_PREFIX, UNRESOLVED_PREFIX, builtin_id/is_builtin/builtin_name, unresolved_attr_id/is_unresolved_attr/unresolved_attr_name, module_of, leaf_name (writes._leaf_name semantics: strip unresolved prefix, then rsplit "." then ":"), strip_shadow/shadow_suffix for $N handling.
2. Migrate consumers, mirroring existing semantics exactly:
   - binder/helpers.py: builtin_symbol_id delegates to models.symbol_id.builtin_id (kept as re-export for binder/references.py and tests).
   - resolve.py: drop _UNRESOLVED_PREFIX; use is_unresolved_attr/unresolved_attr_name; owner_id.split(":",1)[0] -> module_of.
   - query/engine.py: symbol_id.split(":",1)[0] -> module_of (endswith partial-path matching in find_symbol is not leaf-equality, left as-is).
   - analysis/calls.py: drop local UNRESOLVED_PREFIX/BUILTINS_PREFIX; _leaf_method stays a thin wrapper (returns None for non-attribute refs; "."-before-":" branch order preserved) composing the shared primitives.
   - analysis/writes.py: drop local UNRESOLVED_PREFIX and _leaf_name; use shared leaf_name + is_unresolved_attr.
   - check/rules.py: hardcoded "<unresolved>." -> is_unresolved_attr.
   - refactor/extract.py: _local_name -> shared leaf_name (identical for colon-bearing local ids that rdf.inputs/outputs always are).
3. Do NOT touch binder/binder.py, scope_stack.py, refactor/inline.py, purity.py etc. (owned by other agents / out of scope).
4. Add tests/test_symbol_id.py with focused unit tests for every helper incl. shadow-suffix edge cases.
5. Run uv run pytest -q; verify zero failures attributable to this change.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Added src/pypeeker/models/symbol_id.py: BUILTINS_PREFIX, UNRESOLVED_PREFIX, builtin_id/is_builtin/builtin_name, unresolved_attr_id/is_unresolved_attr/unresolved_attr_name, module_of, leaf_name, strip_shadow, shadow_suffix. Leaf module — imports nothing from pypeeker.
- Migrated consumers, mirroring existing semantics exactly:
  - binder/helpers.py: builtin_symbol_id now delegates to models.symbol_id.builtin_id (name kept — binder/references.py and tests import it from helpers; no change needed there).
  - resolve.py: removed _UNRESOLVED_PREFIX class attr; resolve_reference uses is_unresolved_attr/unresolved_attr_name; _class_from_type_name uses module_of.
  - query/engine.py: members() uses module_of. find_symbol's endswith(":name"/".name") matching left alone — it matches partial paths (e.g. "AuthService.validate"), which is not leaf-name equality.
  - analysis/calls.py: removed local UNRESOLVED_PREFIX/BUILTINS_PREFIX; bare_calls composes is_builtin/builtin_name/is_unresolved_attr. _leaf_method kept as a thin local wrapper (returns None for non-attribute refs; its "." -before- ":" branch order differs from leaf_name for ids like pkg.mod:x, so it was not collapsed into the shared helper).
  - analysis/writes.py: removed local UNRESOLVED_PREFIX and _leaf_name; uses shared leaf_name + is_unresolved_attr (shared leaf_name has identical semantics — it was lifted from writes verbatim).
  - check/rules.py: hardcoded "<unresolved>." in no_unresolved_refs -> is_unresolved_attr.
  - refactor/extract.py: removed _local_name; uses shared leaf_name (identical for the colon-bearing local ids rdf.inputs/outputs always carry).
- Out of scope (other agents / construction side left in binder): binder/binder.py builtins_prefix local, binder/scope_stack.py $N construction, refactor/inline.py "$" check, binder/references.py f"<unresolved>.{attr}" construction (instructed: import-line change only, which turned out unnecessary).
- models/__init__.py left empty, matching the existing convention of direct module imports (pypeeker.models.symbol_id).
- Added tests/test_symbol_id.py (24 tests covering every helper incl. shadow edge cases). Full suite: 567 passed, 10 skipped, 0 failures.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Centralized the symbol-id grammar (module.path:Scope.Chain:local$N plus the <builtins>./<unresolved>. sentinel prefixes) in a new leaf module, replacing six ad-hoc re-implementations.

Changes:
- New src/pypeeker/models/symbol_id.py owning the grammar: prefix constants (BUILTINS_PREFIX, UNRESOLVED_PREFIX), sentinel construction/inspection (builtin_id/is_builtin/builtin_name, unresolved_attr_id/is_unresolved_attr/unresolved_attr_name), parsing (module_of, leaf_name), and shadow-suffix handling (strip_shadow, shadow_suffix). Imports nothing outside models, preserving layering.
- resolve.py, query/engine.py, analysis/calls.py, analysis/writes.py, check/rules.py, refactor/extract.py, binder/helpers.py now consume the shared module; the duplicated UNRESOLVED_PREFIX/BUILTINS_PREFIX constants and the writes._leaf_name / extract._local_name copies are gone. binder/helpers.builtin_symbol_id is kept as a thin delegation so binder callers and existing tests are untouched.
- analysis/calls._leaf_method stays as a thin wrapper composing the shared primitives: it intentionally returns None for non-attribute refs and preserves its existing separator branch order, which differs subtly from leaf_name.

Tests:
- New tests/test_symbol_id.py (24 unit tests for every helper, incl. $N edge cases).
- Full suite: uv run pytest -q -> 567 passed, 10 skipped, 0 failures; no behavioral change.

Follow-ups: construction sites in binder (scope_stack.py $N suffixes, references.py <unresolved>. f-strings, binder.py builtins_prefix local) and refactor/inline.py's "$" check were owned by concurrent work and can adopt the module later.
<!-- SECTION:FINAL_SUMMARY:END -->
