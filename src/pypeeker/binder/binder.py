"""Core binder: walks a tree-sitter CST and produces a FileIndex.

The implementation is split across topical modules in this package:

* :mod:`pypeeker.binder.scopes` — function/class/lambda/comprehension
* :mod:`pypeeker.binder.assignments` — assignment, walrus, for/with/except
* :mod:`pypeeker.binder.imports` — import statements, global/nonlocal
* :mod:`pypeeker.binder.references` — identifier/call/attribute uses
* :mod:`pypeeker.binder.helpers` — pure utility functions (no state)

This file owns the top-level entry point :func:`bind`, the dispatch table
:func:`visit_node`, and the module-level :func:`visit_module` orchestrator.
"""

from __future__ import annotations

from tree_sitter import Node

from pypeeker.adapters.python_adapter import PythonAdapter
from pypeeker.binder.assignments import (
    visit_assignment,
    visit_augmented_assignment,
    visit_except_clause,
    visit_for_statement,
    visit_named_expression,
    visit_with_statement,
)
from pypeeker.binder.helpers import compute_hash, make_span
from pypeeker.binder.imports import (
    visit_global_statement,
    visit_import_from_statement,
    visit_import_statement,
    visit_nonlocal_statement,
)
from pypeeker.binder.references import (
    visit_attribute,
    visit_call,
    visit_identifier,
)
from pypeeker.binder.scopes import (
    visit_class_definition,
    visit_comprehension,
    visit_decorated_definition,
    visit_function_definition,
    visit_lambda,
)
from pypeeker.binder.state import BinderState
from pypeeker.models.index import FileIndex
from pypeeker.models.scopes import Scope, ScopeKind


def bind(adapter: PythonAdapter, file_path: str, source: bytes, root: Node) -> FileIndex:
    """Walk the CST and produce a FileIndex.

    The public entry point. Builds a :class:`BinderState`, runs the visitor
    dispatch from the module root, and assembles the result.
    """
    state = BinderState(adapter=adapter, file_path=file_path, source=source)
    visit_module(state, root)
    return FileIndex(
        file_path=state.file_path,
        file_hash=compute_hash(state.source),
        language=state.adapter.language_name,
        symbols=state.symbols,
        scopes=state.scopes,
        references=state.references,
        errors=state.errors,
    )


def visit_module(state: BinderState, node: Node) -> None:
    scope = Scope(
        scope_id=state.file_path,
        name=state.file_path,
        kind=ScopeKind.MODULE,
        file_path=state.file_path,
        span=make_span(node),
    )
    state.scopes.append(scope)
    state.scope_stack.push(scope)
    for child in node.children:
        visit_node(state, child)
    state.scope_stack.pop()


def visit_node(state: BinderState, node: Node) -> None:
    """Dispatch to the appropriate handler based on node type."""
    node_type = node.type

    if node_type == "function_definition":
        visit_function_definition(state, node)
    elif node_type == "class_definition":
        visit_class_definition(state, node)
    elif node_type == "decorated_definition":
        visit_decorated_definition(state, node)
    elif node_type == "assignment":
        visit_assignment(state, node)
    elif node_type == "augmented_assignment":
        visit_augmented_assignment(state, node)
    elif node_type == "named_expression":
        visit_named_expression(state, node)
    elif node_type == "for_statement":
        visit_for_statement(state, node)
    elif node_type == "with_statement":
        visit_with_statement(state, node)
    elif node_type == "except_clause":
        visit_except_clause(state, node)
    elif node_type == "import_statement":
        visit_import_statement(state, node)
    elif node_type == "import_from_statement":
        visit_import_from_statement(state, node)
    elif node_type == "global_statement":
        visit_global_statement(state, node)
    elif node_type == "nonlocal_statement":
        visit_nonlocal_statement(state, node)
    elif node_type == "lambda":
        visit_lambda(state, node)
    elif node_type in (
        "list_comprehension",
        "set_comprehension",
        "dictionary_comprehension",
        "generator_expression",
    ):
        visit_comprehension(state, node)
    elif node_type == "identifier" and id(node) not in state.declaration_nodes:
        visit_identifier(state, node)
    elif node_type == "call":
        visit_call(state, node)
    elif node_type == "attribute":
        visit_attribute(state, node)
    else:
        for child in node.children:
            visit_node(state, child)
