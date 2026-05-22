"""Visitor functions for import statements + global/nonlocal declarations.

All functions take :class:`BinderState` as their first argument and mutate
it to record symbols and references.
"""

from __future__ import annotations

from tree_sitter import Node

from pypeeker.binder.helpers import make_location, resolve_relative_import
from pypeeker.binder.state import BinderState
from pypeeker.models.symbols import Symbol, SymbolKind


def visit_import_statement(state: BinderState, node: Node) -> None:
    """Handle ``import x`` and ``import x as y``."""
    for child in node.children:
        if child.type == "dotted_name":
            name = child.text.decode("utf-8")
            declare_import(state, child, name, name)
        elif child.type == "aliased_import":
            module_node = child.child_by_field_name("name")
            alias_node = child.child_by_field_name("alias")
            if module_node and alias_node:
                declare_import(
                    state,
                    alias_node,
                    alias_node.text.decode("utf-8"),
                    module_node.text.decode("utf-8"),
                    imported_name_node=module_node,
                )
            elif module_node:
                name = module_node.text.decode("utf-8")
                declare_import(state, module_node, name, name)


def visit_import_from_statement(state: BinderState, node: Node) -> None:
    """Handle ``from x import y`` (incl. ``from __future__ import ...``)."""
    module_node = node.child_by_field_name("module_name")
    if module_node:
        module_name = module_node.text.decode("utf-8")
    elif node.type == "future_import_statement":
        # tree-sitter parses ``from __future__ import X`` as its own node type
        # with no ``module_name`` field — the ``__future__`` token sits inline.
        module_name = "__future__"
    else:
        module_name = ""

    module_name = resolve_relative_import(state.file_path, module_name)

    for child in node.children:
        if child.type == "dotted_name" and child != module_node:
            name = child.text.decode("utf-8")
            declare_import(state, child, name, f"{module_name}.{name}")
        elif child.type == "aliased_import":
            import_name_node = child.child_by_field_name("name")
            alias_node = child.child_by_field_name("alias")
            if import_name_node and alias_node:
                declare_import(
                    state,
                    alias_node,
                    alias_node.text.decode("utf-8"),
                    f"{module_name}.{import_name_node.text.decode('utf-8')}",
                    imported_name_node=import_name_node,
                )
            elif import_name_node:
                name = import_name_node.text.decode("utf-8")
                declare_import(
                    state, import_name_node, name, f"{module_name}.{name}"
                )
        elif child.type == "identifier" and child != module_node:
            # Direct identifier import (e.g., ``from os import path``)
            if child.prev_sibling and child.prev_sibling.type == "import":
                name = child.text.decode("utf-8")
                declare_import(state, child, name, f"{module_name}.{name}")


def declare_import(
    state: BinderState,
    node: Node,
    local_name: str,
    module_path: str,
    imported_name_node: Node | None = None,
) -> None:
    """Record an IMPORT symbol in the current scope."""
    state.declaration_nodes.add(id(node))
    scope = state.scope_stack.current_scope
    visibility, vis_confidence = state.adapter.get_visibility(local_name)
    symbol_id = state.scope_stack.build_symbol_id(state.file_path, local_name)

    imported_name_location = None
    if imported_name_node is not None and imported_name_node != node:
        imported_name_location = make_location(state.file_path, imported_name_node)

    symbol = Symbol(
        symbol_id=symbol_id,
        name=local_name,
        kind=SymbolKind.IMPORT,
        location=make_location(state.file_path, node),
        visibility=visibility,
        visibility_confidence=vis_confidence,
        parent_scope_id=scope.scope_id,
        imported_from=module_path,
        imported_name_location=imported_name_location,
    )
    final_id = state.scope_stack.declare(local_name, symbol)
    state.symbols.append(symbol)
    scope.symbol_ids.append(final_id)


def visit_global_statement(state: BinderState, node: Node) -> None:
    """Record names declared ``global`` so later assignments redirect to module scope."""
    for child in node.children:
        if child.type == "identifier":
            name = child.text.decode("utf-8")
            state.scope_stack.current.globals_declared.add(name)
            state.declaration_nodes.add(id(child))


def visit_nonlocal_statement(state: BinderState, node: Node) -> None:
    """Record names declared ``nonlocal`` so later assignments redirect to the enclosing scope."""
    for child in node.children:
        if child.type == "identifier":
            name = child.text.decode("utf-8")
            state.scope_stack.current.nonlocals_declared.add(name)
            state.declaration_nodes.add(id(child))
