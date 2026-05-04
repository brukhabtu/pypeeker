---
id: TASK-17
title: 'Refactor binder: free functions over BinderState (keep Binder facade)'
status: To Do
assignee: []
created_date: '2026-05-04 13:04'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The Binder class is a transient one-shot AST visitor that exists only as a wrapper around shared mutable state (scope_stack, references, symbols, scopes, declaration_nodes). Since the binder is never persisted and never long-lived, it fits the function-with-state-dataclass pattern better than a class — and the file has grown to ~1100 lines in one class, hostile to navigation and extension. This refactor moves all visitor logic into free functions taking a BinderState, organized into topical modules. The Binder class is kept as a thin facade in THIS task to avoid touching call sites; TASK-18 removes it. Behavior must be byte-identical: same FileIndex output for every input.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 src/pypeeker/binder/state.py: BinderState frozen dataclass holding scope_stack, references, symbols, scopes, declaration_nodes, errors, adapter, file_path, source
- [ ] #2 src/pypeeker/binder/helpers.py: pure utility free functions (make_location, make_span, build_symbol_id_for_scope) — no state argument
- [ ] #3 src/pypeeker/binder/scopes.py: free functions for function_definition, class_definition, decorated_definition, lambda, comprehension, parameters, declare_parameter, extract_docstring (each taking BinderState as first arg)
- [ ] #4 src/pypeeker/binder/assignments.py: free functions for assignment, augmented_assignment, named_expression, for_statement, with_statement, with_item, except_clause, extract_targets, declare_variable, make_variable_symbol, determine_reference_kind
- [ ] #5 src/pypeeker/binder/imports.py: free functions for import_statement, import_from_statement, declare_import, resolve_relative_import, global_statement, nonlocal_statement
- [ ] #6 src/pypeeker/binder/references.py: free functions for identifier, call, attribute_call, attribute, receiver_metadata, resolve_self_attribute, determine_attribute_ref_kind
- [ ] #7 src/pypeeker/binder/dispatch.py (or in binder.py): module-level visit_node(state, node) and bind(adapter, file_path, source, root) -> FileIndex
- [ ] #8 src/pypeeker/binder/binder.py: reduced to under 200 lines; Binder class kept as a thin wrapper whose bind() method calls the module-level bind()
- [ ] #9 All 287 existing tests pass without modification
- [ ] #10 Re-indexing pypeeker's own src/ produces byte-identical JSON output to before the refactor
<!-- AC:END -->
