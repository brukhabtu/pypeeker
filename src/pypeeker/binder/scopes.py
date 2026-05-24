"""Visitor functions for scope-creating constructs.

Functions, methods, classes, lambdas, comprehensions — anything that
introduces a new lexical scope. Also covers parameter declaration and
docstring extraction.
"""

from __future__ import annotations

from tree_sitter import Node

from pypeeker.binder.assignments import declare_variable
from pypeeker.binder.helpers import (
    extract_docstring,
    extract_targets,
    make_location,
    make_span,
)
from pypeeker.binder.state import BinderState
from pypeeker.models.capabilities import Confidence
from pypeeker.models.scopes import Scope, ScopeKind
from pypeeker.models.symbols import Symbol, SymbolKind, TypeAnnotation


def visit_function_definition(
    state: BinderState,
    node: Node,
    decorators: list[str] | None = None,
) -> None:
    """Declare the function symbol, open its scope, and walk parameters + body."""
    from pypeeker.binder.binder import visit_node

    name_node = node.child_by_field_name("name")
    if not name_node:
        return
    name = name_node.text.decode("utf-8")

    parent_scope = state.scope_stack.current_scope
    kind = (
        SymbolKind.METHOD
        if parent_scope.kind == ScopeKind.CLASS
        else SymbolKind.FUNCTION
    )

    visibility, vis_confidence = state.adapter.get_visibility(name)

    return_type_node = node.child_by_field_name("return_type")
    type_ann = None
    if return_type_node:
        type_ann = TypeAnnotation(
            raw=return_type_node.text.decode("utf-8"),
            confidence=Confidence.DECLARED,
        )
        # Bind identifiers in the annotation (evaluated in the enclosing scope,
        # before the function's own scope is pushed) so types used only in
        # annotations are tracked references.
        visit_node(state, return_type_node)

    docstring = extract_docstring(node)

    symbol_id = state.scope_stack.build_symbol_id(
        state.module_path, name, is_scope_creator=True
    )
    symbol = Symbol(
        symbol_id=symbol_id,
        name=name,
        kind=kind,
        location=make_location(state.file_path, name_node),
        visibility=visibility,
        visibility_confidence=vis_confidence,
        type_annotation=type_ann,
        decorators=decorators or [],
        docstring=docstring,
        parent_scope_id=parent_scope.scope_id,
    )
    final_id = state.scope_stack.declare(name, symbol)
    state.symbols.append(symbol)
    parent_scope.symbol_ids.append(final_id)
    state.declaration_nodes.add(id(name_node))

    scope = Scope(
        scope_id=final_id,
        name=name,
        kind=ScopeKind.FUNCTION,
        file_path=state.file_path,
        span=make_span(node),
        parent_scope_id=parent_scope.scope_id,
    )
    state.scopes.append(scope)
    parent_scope.child_scope_ids.append(scope.scope_id)
    state.scope_stack.push(scope)

    params_node = node.child_by_field_name("parameters")
    if params_node:
        visit_parameters(state, params_node)

    body_node = node.child_by_field_name("body")
    if body_node:
        for child in body_node.children:
            visit_node(state, child)

    state.scope_stack.pop()


def visit_class_definition(
    state: BinderState,
    node: Node,
    decorators: list[str] | None = None,
) -> None:
    """Declare the class symbol, open its scope, and walk body declarations."""
    from pypeeker.binder.binder import visit_node

    name_node = node.child_by_field_name("name")
    if not name_node:
        return
    name = name_node.text.decode("utf-8")

    parent_scope = state.scope_stack.current_scope
    visibility, vis_confidence = state.adapter.get_visibility(name)
    docstring = extract_docstring(node)

    symbol_id = state.scope_stack.build_symbol_id(
        state.module_path, name, is_scope_creator=True
    )
    symbol = Symbol(
        symbol_id=symbol_id,
        name=name,
        kind=SymbolKind.CLASS,
        location=make_location(state.file_path, name_node),
        visibility=visibility,
        visibility_confidence=vis_confidence,
        decorators=decorators or [],
        docstring=docstring,
        parent_scope_id=parent_scope.scope_id,
    )
    final_id = state.scope_stack.declare(name, symbol)
    state.symbols.append(symbol)
    parent_scope.symbol_ids.append(final_id)
    state.declaration_nodes.add(id(name_node))

    superclasses_node = node.child_by_field_name("superclasses")
    if superclasses_node:
        for child in superclasses_node.children:
            visit_node(state, child)

    scope = Scope(
        scope_id=final_id,
        name=name,
        kind=ScopeKind.CLASS,
        file_path=state.file_path,
        span=make_span(node),
        parent_scope_id=parent_scope.scope_id,
    )
    state.scopes.append(scope)
    parent_scope.child_scope_ids.append(scope.scope_id)
    state.scope_stack.push(scope)

    body_node = node.child_by_field_name("body")
    if body_node:
        for child in body_node.children:
            visit_node(state, child)

    state.scope_stack.pop()


def visit_decorated_definition(state: BinderState, node: Node) -> None:
    """Extract decorators, then visit the inner function/class definition."""
    from pypeeker.binder.binder import visit_node

    decorators: list[str] = []
    definition_node = None

    for child in node.children:
        if child.type == "decorator":
            dec_text = child.text.decode("utf-8").lstrip("@").strip()
            decorators.append(dec_text)
            for dec_child in child.children:
                if dec_child.type != "@":
                    visit_node(state, dec_child)
        elif child.type in ("function_definition", "class_definition"):
            definition_node = child

    if definition_node:
        if definition_node.type == "function_definition":
            visit_function_definition(state, definition_node, decorators=decorators)
        elif definition_node.type == "class_definition":
            visit_class_definition(state, definition_node, decorators=decorators)


def visit_lambda(state: BinderState, node: Node) -> None:
    """Open a lambda scope, declare parameters, and visit the body expression."""
    from pypeeker.binder.binder import visit_node

    parent_scope = state.scope_stack.current_scope
    scope = Scope(
        scope_id=(
            f"{state.scope_stack.build_scope_chain(state.module_path)}"
            f":<lambda:{node.start_point[0]}>"
        ),
        name="<lambda>",
        kind=ScopeKind.LAMBDA,
        file_path=state.file_path,
        span=make_span(node),
        parent_scope_id=parent_scope.scope_id,
    )
    state.scopes.append(scope)
    parent_scope.child_scope_ids.append(scope.scope_id)
    state.scope_stack.push(scope)

    params_node = node.child_by_field_name("parameters")
    if params_node:
        visit_parameters(state, params_node)

    body_node = node.child_by_field_name("body")
    if body_node:
        visit_node(state, body_node)

    state.scope_stack.pop()


def visit_comprehension(state: BinderState, node: Node) -> None:
    """Bind a list / set / dict comp or generator expression.

    Python semantics: the FIRST iterable is evaluated in the enclosing scope,
    every subsequent iterable plus the element / filter expressions are
    evaluated inside the comprehension scope with the loop targets bound.

    Tree-sitter emits children in source order — element first, then
    ``for_in_clause`` nodes, then ``if_clause`` nodes — so a naive left-to-right
    walk visits the element before the targets are declared and the element's
    identifiers come out unresolved. We do two passes: first process every
    ``for_in_clause`` (declare its targets, visit its iterable in the
    appropriate scope), then visit the element and any ``if_clause`` filters.
    """
    from pypeeker.binder.binder import visit_node

    parent_scope = state.scope_stack.current_scope
    scope = Scope(
        scope_id=(
            f"{state.scope_stack.build_scope_chain(state.module_path)}"
            f":<comp:{node.start_point[0]}>"
        ),
        name="<comprehension>",
        kind=ScopeKind.COMPREHENSION,
        file_path=state.file_path,
        span=make_span(node),
        parent_scope_id=parent_scope.scope_id,
    )
    state.scopes.append(scope)
    parent_scope.child_scope_ids.append(scope.scope_id)
    state.scope_stack.push(scope)

    # Pass 1: process every for_in_clause so targets are declared before any
    # body expression references them.
    first_for = True
    for child in node.children:
        if child.type != "for_in_clause":
            continue
        left = child.child_by_field_name("left")
        right = child.child_by_field_name("right")
        if right is not None:
            if first_for:
                # First iterable lives in the enclosing scope.
                state.scope_stack.pop()
                visit_node(state, right)
                state.scope_stack.push(scope)
            else:
                visit_node(state, right)
        if left is not None:
            for target_node in extract_targets(left):
                declare_variable(
                    state, target_node, target_node.text.decode("utf-8")
                )
        first_for = False

    # Pass 2: element expression and filter clauses.
    for child in node.children:
        if child.type in ("for_in_clause", "[", "]", "{", "}", "(", ")"):
            continue
        if child.type == "if_clause":
            for if_child in child.children:
                if if_child.type != "if":
                    visit_node(state, if_child)
            continue
        visit_node(state, child)

    state.scope_stack.pop()


def visit_parameters(state: BinderState, node: Node) -> None:
    """Extract parameters from a function's parameter list."""
    from pypeeker.binder.binder import visit_node

    for child in node.children:
        if child.type == "identifier":
            name = child.text.decode("utf-8")
            declare_parameter(state, child, name)
        elif child.type in ("default_parameter", "typed_default_parameter"):
            name_node = child.child_by_field_name("name")
            if name_node:
                name = name_node.text.decode("utf-8")
                type_node = child.child_by_field_name("type")
                type_ann = None
                if type_node:
                    type_ann = TypeAnnotation(
                        raw=type_node.text.decode("utf-8"),
                        confidence=Confidence.DECLARED,
                    )
                declare_parameter(state, name_node, name, type_ann)
                state.declaration_nodes.add(id(name_node))
                if type_node:
                    visit_node(state, type_node)
            value_node = child.child_by_field_name("value")
            if value_node:
                visit_node(state, value_node)
        elif child.type == "typed_parameter":
            name_node = None
            for tc in child.children:
                if tc.type == "identifier":
                    name_node = tc
                    break
            if name_node:
                name = name_node.text.decode("utf-8")
                type_node = child.child_by_field_name("type")
                type_ann = None
                if type_node:
                    type_ann = TypeAnnotation(
                        raw=type_node.text.decode("utf-8"),
                        confidence=Confidence.DECLARED,
                    )
                declare_parameter(state, name_node, name, type_ann)
                state.declaration_nodes.add(id(name_node))
                if type_node:
                    visit_node(state, type_node)
        elif child.type in ("list_splat_pattern", "dictionary_splat_pattern"):
            for splat_child in child.children:
                if splat_child.type == "identifier":
                    name = splat_child.text.decode("utf-8")
                    declare_parameter(state, splat_child, name)
                    state.declaration_nodes.add(id(splat_child))


def declare_parameter(
    state: BinderState,
    node: Node,
    name: str,
    type_ann: TypeAnnotation | None = None,
) -> None:
    """Declare a function parameter symbol in the current scope."""
    state.declaration_nodes.add(id(node))
    scope = state.scope_stack.current_scope
    visibility, vis_confidence = state.adapter.get_visibility(name)
    symbol_id = state.scope_stack.build_symbol_id(state.module_path, name)

    symbol = Symbol(
        symbol_id=symbol_id,
        name=name,
        kind=SymbolKind.PARAMETER,
        location=make_location(state.file_path, node),
        visibility=visibility,
        visibility_confidence=vis_confidence,
        type_annotation=type_ann,
        parent_scope_id=scope.scope_id,
    )
    final_id = state.scope_stack.declare(name, symbol)
    state.symbols.append(symbol)
    scope.symbol_ids.append(final_id)
