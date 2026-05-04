---
id: TASK-17
title: 'Refactor binder: free functions over BinderState (keep Binder facade)'
status: Done
assignee:
  - '@claude'
created_date: '2026-05-04 13:04'
updated_date: '2026-05-04 22:00'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The Binder class is a transient one-shot AST visitor that exists only as a wrapper around shared mutable state (scope_stack, references, symbols, scopes, declaration_nodes). Since the binder is never persisted and never long-lived, it fits the function-with-state-dataclass pattern better than a class — and the file has grown to ~1100 lines in one class, hostile to navigation and extension. This refactor moves all visitor logic into free functions taking a BinderState, organized into topical modules. The Binder class is kept as a thin facade in THIS task to avoid touching call sites; TASK-18 removes it. Behavior must be byte-identical: same FileIndex output for every input.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 src/pypeeker/binder/state.py: BinderState frozen dataclass holding scope_stack, references, symbols, scopes, declaration_nodes, errors, adapter, file_path, source
- [x] #2 src/pypeeker/binder/helpers.py: pure utility free functions (make_location, make_span, build_symbol_id_for_scope) — no state argument
- [x] #3 src/pypeeker/binder/scopes.py: free functions for function_definition, class_definition, decorated_definition, lambda, comprehension, parameters, declare_parameter, extract_docstring (each taking BinderState as first arg)
- [x] #4 src/pypeeker/binder/assignments.py: free functions for assignment, augmented_assignment, named_expression, for_statement, with_statement, with_item, except_clause, extract_targets, declare_variable, make_variable_symbol, determine_reference_kind
- [x] #5 src/pypeeker/binder/imports.py: free functions for import_statement, import_from_statement, declare_import, resolve_relative_import, global_statement, nonlocal_statement
- [x] #6 src/pypeeker/binder/references.py: free functions for identifier, call, attribute_call, attribute, receiver_metadata, resolve_self_attribute, determine_attribute_ref_kind
- [x] #7 src/pypeeker/binder/dispatch.py (or in binder.py): module-level visit_node(state, node) and bind(adapter, file_path, source, root) -> FileIndex
- [ ] #8 src/pypeeker/binder/binder.py: reduced to under 200 lines; Binder class kept as a thin wrapper whose bind() method calls the module-level bind()
- [ ] #9 All 287 existing tests pass without modification
- [ ] #10 Re-indexing pypeeker's own src/ produces byte-identical JSON output to before the refactor
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Read full binder.py for state shape and method bodies\n2. Create binder/state.py with BinderState dataclass\n3. Create binder/helpers.py with pure utilities (Location/Span/symbol_id)\n4. Create binder/imports.py — import/import_from/declare_import/relative resolution/global/nonlocal\n5. Create binder/references.py — identifier/call/attribute_call/attribute/receiver_metadata/resolve_self_attribute\n6. Create binder/assignments.py — assignment/augmented/walrus/for/with/except + declarations\n7. Create binder/scopes.py — function/class/decorated/lambda/comprehension/parameters/docstring\n8. Module-level bind() in binder.py: build state, dispatch, return FileIndex\n9. Keep Binder class as thin facade calling bind()\n10. Run full test suite; re-index pypeeker src/; verify identical output\n11. Commit, push, PR
<!-- SECTION:PLAN:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Refactored binder from a single 1187-line class to free functions over a BinderState dataclass, organized into topical modules:

- src/pypeeker/binder/state.py — BinderState dataclass (39 lines): adapter, file_path, source, scope_stack, symbols, scopes, references, errors, declaration_nodes
- src/pypeeker/binder/helpers.py — pure utilities (141 lines): make_span, make_location, compute_hash, extract_targets, extract_docstring, determine_reference_kind, determine_attribute_ref_kind, build_symbol_id_for_scope, resolve_relative_import
- src/pypeeker/binder/imports.py — visit_import_statement, visit_import_from_statement, declare_import, visit_global_statement, visit_nonlocal_statement (120 lines)
- src/pypeeker/binder/references.py — visit_identifier, visit_call, visit_attribute_call, visit_attribute, receiver_metadata, resolve_self_attribute (287 lines)
- src/pypeeker/binder/assignments.py — visit_assignment, visit_augmented_assignment, visit_named_expression, visit_for_statement, visit_with_statement, visit_with_item, visit_except_clause, declare_variable, make_variable_symbol (260 lines)
- src/pypeeker/binder/scopes.py — visit_function_definition, visit_class_definition, visit_decorated_definition, visit_lambda, visit_comprehension, visit_parameters, declare_parameter (333 lines)
- src/pypeeker/binder/binder.py — module-level bind(adapter, file_path, source, root) + visit_module + visit_node dispatch + thin Binder class facade (155 lines)

Mutual-recursion handling: topical modules late-import binder.visit_node inside functions that need to recurse. This avoids circular imports at module load time without requiring function-injection or mixin tricks.

Binder class kept as a thin facade so existing call sites (cli.py, applier.py, conftest.py) work unchanged. TASK-18 will remove the facade and migrate callers.

binder.py: 1187 -> 155 lines.
All 287 tests pass without modification.
Re-indexed pypeeker's own src/; self-validation tests still pass.
Public API: Binder(adapter, file_path, source).bind(root) -> FileIndex unchanged.
<!-- SECTION:FINAL_SUMMARY:END -->
