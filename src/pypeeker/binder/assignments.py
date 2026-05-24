"""Visitor functions for assignment-like constructs and variable declarations.

Covers ``=``, augmented assignment, walrus, ``for`` loop targets, ``with``
items, ``except`` clause targets, and the lower-level
:func:`declare_variable` / :func:`make_variable_symbol` helpers.
"""

from __future__ import annotations

from tree_sitter import Node

from pypeeker.binder.helpers import (
    build_symbol_id_for_scope,
    extract_targets,
    make_location,
)
from pypeeker.binder.state import BinderState
from pypeeker.models.capabilities import Confidence
from pypeeker.models.references import Reference, ReferenceKind
from pypeeker.models.scopes import ScopeKind
from pypeeker.models.symbols import Symbol, SymbolKind, TypeAnnotation


def visit_assignment(state: BinderState, node: Node) -> None:
    """Handle ``x = expr`` — declare LHS targets, visit RHS for references."""
    from pypeeker.binder.binder import visit_node

    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    type_node = node.child_by_field_name("type")

    type_ann = None
    if type_node:
        type_ann = TypeAnnotation(
            raw=type_node.text.decode("utf-8"),
            confidence=Confidence.DECLARED,
        )

    if left:
        targets = extract_targets(left)
        # With no explicit annotation, infer the type of a single target from a
        # constructor call on the RHS (``x = Foo()``). Recorded at INFERRED
        # confidence; resolution only succeeds if the name turns out to be a
        # class with the accessed member, so over-recording is harmless.
        if type_ann is None and len(targets) == 1:
            ctor = _constructor_type_name(right)
            if ctor is not None:
                type_ann = TypeAnnotation(raw=ctor, confidence=Confidence.INFERRED)
        for target_node in targets:
            name = target_node.text.decode("utf-8")
            declare_variable(state, target_node, name, type_ann)
        # Also visit left for attribute/subscript targets (identifiers
        # skipped via state.declaration_nodes).
        visit_node(state, left)

    if right:
        visit_node(state, right)
    if type_node:
        visit_node(state, type_node)


def _constructor_type_name(node: Node | None) -> str | None:
    """If ``node`` is a call to a simple/dotted name, return that name.

    ``Foo()`` -> ``"Foo"``; ``mod.Foo()`` -> ``"mod.Foo"``. None for any other
    RHS shape (subscripts, awaits, binary ops, calls on expressions, ...).
    """
    if node is None or node.type != "call":
        return None
    fn = node.child_by_field_name("function")
    if fn is None or fn.type not in ("identifier", "attribute"):
        return None
    return fn.text.decode("utf-8")


def visit_augmented_assignment(state: BinderState, node: Node) -> None:
    """Handle ``x += expr``. Read+write, not a new declaration."""
    from pypeeker.binder.binder import visit_node

    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")

    if left and left.type == "identifier":
        name = left.text.decode("utf-8")
        state.declaration_nodes.add(id(left))
        resolved = state.scope_stack.resolve(name)
        if resolved:
            state.references.append(
                Reference(
                    symbol_id=resolved.symbol_id,
                    kind=ReferenceKind.WRITE,
                    location=make_location(state.file_path, left),
                    in_scope_id=state.scope_stack.current_scope.scope_id,
                )
            )
        else:
            state.references.append(
                Reference(
                    symbol_id=name,
                    kind=ReferenceKind.WRITE,
                    location=make_location(state.file_path, left),
                    in_scope_id=state.scope_stack.current_scope.scope_id,
                    resolved=False,
                )
            )

    if right:
        visit_node(state, right)


def visit_named_expression(state: BinderState, node: Node) -> None:
    """Handle the walrus operator ``x := expr``.

    Inside a comprehension, the target binds in the containing function scope.
    """
    from pypeeker.binder.binder import visit_node

    name_node = node.child_by_field_name("name")
    value_node = node.child_by_field_name("value")

    if name_node:
        name = name_node.text.decode("utf-8")
        state.declaration_nodes.add(id(name_node))

        current_kind = state.scope_stack.current_scope.kind
        if current_kind == ScopeKind.COMPREHENSION:
            target_entry = state.scope_stack.find_enclosing_function_entry()
            if target_entry:
                symbol_id = build_symbol_id_for_scope(
                    target_entry.scope, name, state.module_path
                )
                symbol = make_variable_symbol(state, name_node, name, symbol_id)
                state.scope_stack.declare_in_scope(name, symbol, target_entry)
                state.symbols.append(symbol)
                target_entry.scope.symbol_ids.append(symbol.symbol_id)
        else:
            declare_variable(state, name_node, name)

    if value_node:
        visit_node(state, value_node)


def visit_for_statement(state: BinderState, node: Node) -> None:
    """Handle ``for x in iterable:`` — declare loop targets, visit iterable + body."""
    from pypeeker.binder.binder import visit_node

    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    body = node.child_by_field_name("body")
    alternative = node.child_by_field_name("alternative")

    if left:
        targets = extract_targets(left)
        for target_node in targets:
            name = target_node.text.decode("utf-8")
            declare_variable(state, target_node, name)

    if right:
        visit_node(state, right)
    if body:
        for child in body.children:
            visit_node(state, child)
    if alternative:
        for child in alternative.children:
            visit_node(state, child)


def visit_with_statement(state: BinderState, node: Node) -> None:
    """Handle ``with expr as x:`` — declare ``as`` bindings, visit body."""
    from pypeeker.binder.binder import visit_node

    body = node.child_by_field_name("body")

    for child in node.children:
        if child.type == "with_clause":
            for with_item in child.children:
                if with_item.type == "with_item":
                    visit_with_item(state, with_item)

    if body:
        for child in body.children:
            visit_node(state, child)


def visit_with_item(state: BinderState, node: Node) -> None:
    """Handle a single with_item: expression [as target]."""
    from pypeeker.binder.binder import visit_node

    for child in node.children:
        if child.type == "as_pattern":
            for ap_child in child.children:
                if ap_child.type == "as_pattern_target":
                    for target_child in ap_child.children:
                        if target_child.type == "identifier":
                            name = target_child.text.decode("utf-8")
                            declare_variable(state, target_child, name)
                elif ap_child.type not in ("as",):
                    visit_node(state, ap_child)
        else:
            visit_node(state, child)


def visit_except_clause(state: BinderState, node: Node) -> None:
    """Extract exception variable from an except clause."""
    from pypeeker.binder.binder import visit_node

    for child in node.children:
        if child.type == "block":
            for block_child in child.children:
                visit_node(state, block_child)
        elif child.type == "as_pattern":
            for ap_child in child.children:
                if ap_child.type == "as_pattern_target":
                    for target_child in ap_child.children:
                        if target_child.type == "identifier":
                            name = target_child.text.decode("utf-8")
                            declare_variable(state, target_child, name)
                elif ap_child.type == "identifier":
                    visit_node(state, ap_child)
        elif child.type not in ("except", ":", ","):
            visit_node(state, child)


def declare_variable(
    state: BinderState,
    node: Node,
    name: str,
    type_ann: TypeAnnotation | None = None,
) -> None:
    """Declare a variable in the current scope (or redirected via global/nonlocal)."""
    state.declaration_nodes.add(id(node))
    current_entry = state.scope_stack.current

    # Check global/nonlocal redirects.
    if name in current_entry.globals_declared:
        target_entry = state.scope_stack.find_global_target()
        symbol_id = build_symbol_id_for_scope(
            target_entry.scope, name, state.module_path
        )
        symbol = make_variable_symbol(state, node, name, symbol_id, type_ann)
        symbol.parent_scope_id = target_entry.scope.scope_id
        state.scope_stack.declare_in_scope(name, symbol, target_entry)
        state.symbols.append(symbol)
        target_entry.scope.symbol_ids.append(symbol.symbol_id)
        return

    if name in current_entry.nonlocals_declared:
        target_entry = state.scope_stack.find_nonlocal_target(name)
        if target_entry:
            symbol_id = build_symbol_id_for_scope(
                target_entry.scope, name, state.module_path
            )
            symbol = make_variable_symbol(state, node, name, symbol_id, type_ann)
            symbol.parent_scope_id = target_entry.scope.scope_id
            state.scope_stack.declare_in_scope(name, symbol, target_entry)
            state.symbols.append(symbol)
            target_entry.scope.symbol_ids.append(symbol.symbol_id)
            return

    scope = state.scope_stack.current_scope
    symbol_id = state.scope_stack.build_symbol_id(state.module_path, name)
    symbol = make_variable_symbol(state, node, name, symbol_id, type_ann)
    symbol.parent_scope_id = scope.scope_id
    final_id = state.scope_stack.declare(name, symbol)
    state.symbols.append(symbol)
    scope.symbol_ids.append(final_id)


def make_variable_symbol(
    state: BinderState,
    node: Node,
    name: str,
    symbol_id: str,
    type_ann: TypeAnnotation | None = None,
) -> Symbol:
    """Build a VARIABLE Symbol with adapter-derived visibility for ``name``."""
    visibility, vis_confidence = state.adapter.get_visibility(name)
    return Symbol(
        symbol_id=symbol_id,
        name=name,
        kind=SymbolKind.VARIABLE,
        location=make_location(state.file_path, node),
        visibility=visibility,
        visibility_confidence=vis_confidence,
        type_annotation=type_ann,
    )
