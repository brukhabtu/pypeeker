---
id: TASK-26
title: Add missing docstrings to public APIs flagged by require-docstrings
status: Done
assignee:
  - '@claude'
created_date: '2026-05-22 22:45'
updated_date: '2026-05-22 22:49'
labels:
  - docs
  - linter
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
pypeeker check on its own source reports 50 missing public docstrings. Most are abstract-method stubs in adapters/base.py whose concrete implementations have docstrings, plus internal helpers in binder/ that grew out of refactors without inheriting docs. Goal: bring the project to zero pypeeker check violations.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 src/pypeeker/adapters/base.py: docstrings on every abstract public method (language_name, capabilities, parse, is_scope_node, is_declaration_node, is_reference_node, extract_name, get_visibility, get_type_annotation)
- [x] #2 src/pypeeker/adapters/python_adapter.py: same 9 methods have docstrings (concrete implementations)
- [x] #3 src/pypeeker/binder/scope_stack.py: docstrings on all 9 flagged public methods
- [x] #4 src/pypeeker/binder/scopes.py, assignments.py, helpers.py, imports.py, binder.py: docstrings on all flagged public functions
- [x] #5 src/pypeeker/storage/transaction_store.py, index_store.py: docstrings on flagged properties/methods
- [x] #6 src/pypeeker/models/*.py: docstrings on flagged dataclasses (Visibility, ReferenceKind, ScopeKind)
- [x] #7 src/pypeeker/check/engine.py + src/pypeeker/indexer.py: docstrings on flagged methods
- [x] #8 pypeeker check on the project's own source exits 0
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Walk file-by-file, add concise one-line docstrings to each flagged symbol. Prefer brevity — these are mostly self-explanatory accessors/dispatch helpers.
2. Re-run pypeeker check after each batch to confirm progress.
3. Run pytest periodically to catch any accidental code changes.
4. Final: pypeeker check exits 0 on its own source.
5. Commit, PR, merge.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Added 50 one-line docstrings across 13 files. Each docstring explains WHY the method exists rather than restating the signature. Re-indexed and pypeeker check now exits 0.

Full suite still 361 passed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Add concise docstrings to every public API flagged by \`require-docstrings\`.

50 docstrings across:
- src/pypeeker/adapters/base.py (9 protocol method stubs)
- src/pypeeker/adapters/python_adapter.py (9 concrete impls)
- src/pypeeker/binder/scope_stack.py (9 stack ops + properties)
- src/pypeeker/binder/scopes.py (4 scope-opening visitors + declare_parameter)
- src/pypeeker/binder/assignments.py (4 statement visitors + make_variable_symbol)
- src/pypeeker/binder/helpers.py (3 small utilities)
- src/pypeeker/binder/imports.py (global/nonlocal visitors)
- src/pypeeker/binder/binder.py (visit_module)
- src/pypeeker/check/engine.py (run)
- src/pypeeker/indexer.py (IndexResult.to_dict)
- src/pypeeker/storage/index_store.py + transaction_store.py (project_root / root properties)
- src/pypeeker/models/references.py + scopes.py + symbols.py (ReferenceKind, ScopeKind, SymbolKind, Visibility enums)

Every docstring is one line, focused on what the symbol exists FOR rather than restating its signature.

## Verification
\`\`\`
$ rm -rf .semantic-tool && pypeeker index src/ && pypeeker check
$ echo $?
0
\`\`\`

Full suite: 361 passed.
<!-- SECTION:FINAL_SUMMARY:END -->
