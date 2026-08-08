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
    node_key,
    make_location,
    make_span,
)
from pypeeker.binder.state import BinderState
from pypeeker.models import Confidence, Scope, ScopeKind, Symbol, SymbolKind, TypeAnnotation


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

    type_params_node = node.child_by_field_name("type_parameters")

    return_type_node = node.child_by_field_name("return_type")
    type_ann = None
    if return_type_node:
        type_ann = TypeAnnotation(
            raw=return_type_node.text.decode("utf-8"),
            confidence=Confidence.DECLARED,
        )
        if type_params_node is None:
            # Bind identifiers in the annotation (evaluated in the enclosing
            # scope, before the function's own scope is pushed) so types used
            # only in annotations are tracked references. When a PEP 695
            # type-parameter list is present the annotation is bound below
            # instead — still in this enclosing scope, but with the type
            # parameters overlaid on it.
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
    state.declaration_nodes.add(node_key(name_node))

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

    if type_params_node is not None:
        # The type parameters belong to this function's scope, but the header
        # they scope over (their own bounds, plus the return annotation) is
        # evaluated in the enclosing scope — see `_bind_generic_header`.
        header: list[Node] = []
        if return_type_node is not None:
            header.append(return_type_node)
        _bind_generic_header(state, type_params_node, header)

    params_node = node.child_by_field_name("parameters")
    if params_node:
        _visit_parameters(state, params_node)

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
    state.declaration_nodes.add(node_key(name_node))

    type_params_node = node.child_by_field_name("type_parameters")

    superclasses_node = node.child_by_field_name("superclasses")
    if superclasses_node and type_params_node is None:
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

    if type_params_node is not None:
        # A PEP 695 type-parameter list scopes over the base-class list, so
        # the bases could not be visited above — but they must still be bound
        # in the *parent* scope: `analysis/hierarchy.py` discriminates a base
        # reference from any other header reference by
        # `ref.in_scope_id == class_symbol.parent_scope_id`, and CPython
        # resolves a base name against the enclosing class namespace.
        # `_bind_generic_header` binds them there with the type parameters
        # overlaid, which satisfies both.
        header: list[Node] = []
        if superclasses_node:
            header.extend(superclasses_node.children)
        _bind_generic_header(state, type_params_node, header)

    body_node = node.child_by_field_name("body")
    if body_node:
        for child in body_node.children:
            visit_node(state, child)

    state.scope_stack.pop()


def visit_type_alias_statement(state: BinderState, node: Node) -> None:
    """Bind a PEP 695 ``type X = ...`` / ``type X[T] = ...`` statement.

    Unlike a function or class, a type alias owns no scope of its own, so a
    generic alias gets an explicit :class:`Scope` of kind ``TYPE_PARAMS`` to
    hold its type parameters; a non-generic alias declares the name and visits
    its value directly, with no new scope. Either way the value itself is
    bound in the enclosing scope — for the generic case with the type
    parameters overlaid, see :func:`_bind_generic_header`.

    The alias name is declared *before* the right-hand side is visited, so a
    self-referential alias (``type Json = list[Json] | int``) resolves — PEP
    695 alias values are lazily evaluated, so this is the faithful order,
    not just a convenience.
    """
    from pypeeker.binder.binder import visit_node

    left_node = node.child_by_field_name("left")
    right_node = node.child_by_field_name("right")
    if left_node is None or not left_node.named_children:
        # Unrecognized shape — fall back to a plain walk so nothing is
        # silently dropped.
        for child in node.children:
            visit_node(state, child)
        return

    head = left_node.named_children[0]
    type_params_node: Node | None = None
    if head.type == "identifier":
        name_node = head
    elif head.type == "generic_type" and head.named_children:
        name_node = head.named_children[0]
        if len(head.named_children) > 1:
            type_params_node = head.named_children[1]
    else:
        for child in node.children:
            visit_node(state, child)
        return

    if name_node.type != "identifier":
        for child in node.children:
            visit_node(state, child)
        return

    name = name_node.text.decode("utf-8")
    parent_scope = state.scope_stack.current_scope
    visibility, vis_confidence = state.adapter.get_visibility(name)
    symbol_id = state.scope_stack.build_symbol_id(
        state.module_path, name, is_scope_creator=True
    )
    symbol = Symbol(
        symbol_id=symbol_id,
        name=name,
        kind=SymbolKind.VARIABLE,
        location=make_location(state.file_path, name_node),
        visibility=visibility,
        visibility_confidence=vis_confidence,
        parent_scope_id=parent_scope.scope_id,
    )
    final_id = state.scope_stack.declare(name, symbol)
    state.symbols.append(symbol)
    parent_scope.symbol_ids.append(final_id)
    state.declaration_nodes.add(node_key(name_node))

    if type_params_node is None:
        if right_node is not None:
            visit_node(state, right_node)
        return

    scope = Scope(
        scope_id=final_id,
        name=name,
        kind=ScopeKind.TYPE_PARAMS,
        file_path=state.file_path,
        span=make_span(node),
        parent_scope_id=parent_scope.scope_id,
    )
    state.scopes.append(scope)
    parent_scope.child_scope_ids.append(scope.scope_id)
    state.scope_stack.push(scope)

    # The alias value is the header here: bound back in the parent scope with
    # the type parameters overlaid, so `type A[T] = list[Elem]` inside a class
    # body still sees `Elem`. The TYPE_PARAMS scope is not re-entered
    # afterwards — an alias has no body.
    header = [right_node] if right_node is not None else []
    _bind_generic_header(state, type_params_node, header, reenter=False)


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
        _visit_parameters(state, params_node)

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


def _visit_parameters(state: BinderState, node: Node) -> None:
    """Extract parameters from a function's parameter list."""
    from pypeeker.binder.binder import visit_node

    for child in node.children:
        if child.type == "identifier":
            name = child.text.decode("utf-8")
            _declare_parameter(state, child, name)
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
                _declare_parameter(state, name_node, name, type_ann)
                state.declaration_nodes.add(node_key(name_node))
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
                if tc.type in ("list_splat_pattern", "dictionary_splat_pattern"):
                    # `*args: T` / `**kwargs: T`: tree-sitter nests the name
                    # one level down, inside the splat pattern, so the
                    # direct-children scan above never sees it. Left unbound,
                    # every use of the parameter in the body reads as an
                    # unresolved reference — the untyped `*args` spelling
                    # below has always bound correctly.
                    name_node = next(
                        (sc for sc in tc.children if sc.type == "identifier"), None
                    )
                    if name_node is not None:
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
                _declare_parameter(state, name_node, name, type_ann)
                state.declaration_nodes.add(node_key(name_node))
                if type_node:
                    visit_node(state, type_node)
        elif child.type in ("list_splat_pattern", "dictionary_splat_pattern"):
            for splat_child in child.children:
                if splat_child.type == "identifier":
                    name = splat_child.text.decode("utf-8")
                    _declare_parameter(state, splat_child, name)
                    state.declaration_nodes.add(node_key(splat_child))


def _declare_parameter(
    state: BinderState,
    node: Node,
    name: str,
    type_ann: TypeAnnotation | None = None,
) -> None:
    """Declare a function parameter symbol in the current scope."""
    state.declaration_nodes.add(node_key(node))
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


def _declare_type_parameter(state: BinderState, node: Node, name: str) -> Symbol:
    """Declare a PEP 695 inline type parameter (``T`` in ``def f[T]``) in the
    current scope, distinct in kind from an ordinary runtime PARAMETER so
    call-argument and argument-mutation analyses don't mistake it for one."""
    state.declaration_nodes.add(node_key(node))
    scope = state.scope_stack.current_scope
    visibility, vis_confidence = state.adapter.get_visibility(name)
    symbol_id = state.scope_stack.build_symbol_id(state.module_path, name)

    symbol = Symbol(
        symbol_id=symbol_id,
        name=name,
        kind=SymbolKind.TYPE_PARAMETER,
        location=make_location(state.file_path, node),
        visibility=visibility,
        visibility_confidence=vis_confidence,
        parent_scope_id=scope.scope_id,
    )
    final_id = state.scope_stack.declare(name, symbol)
    state.symbols.append(symbol)
    scope.symbol_ids.append(final_id)
    return symbol


def _declare_type_parameters(
    state: BinderState, node: Node
) -> tuple[dict[str, Symbol], list[Node]]:
    """Declare every name in a PEP 695 ``type_parameter`` list (``[T]``,
    ``[T: int]``, ``[*Ts]``, ``[**P]``) in the current scope.

    Returns the name -> symbol map plus the bound/constraint nodes, which the
    caller binds afterwards (they are part of the header, evaluated with every
    type parameter already in scope, so ``def f[T: int, U: T]`` resolves ``T``
    regardless of source order).

    If the list itself carries a parse error — PEP 696 defaults
    (``def d[T = int]``) are unparseable under the pinned tree-sitter-python
    grammar and surface as an ``ERROR`` node holding the name plus a stray
    ``type`` node holding the default — bail out entirely rather than bind
    whatever fragments survived; a bogus binding (e.g. ``int`` shadowed as a
    type parameter) would be worse than the syntax error already recorded in
    ``FileIndex.errors``.
    """
    declared: dict[str, Symbol] = {}
    bounds: list[Node] = []

    if node.has_error:
        return declared, bounds

    for type_node in node.named_children:
        if type_node.type != "type":
            continue
        inner = type_node.named_children[0] if type_node.named_children else None
        if inner is None:
            continue
        name_node: Node | None = None
        bound_node: Node | None = None
        if inner.type == "identifier":
            name_node = inner
        elif inner.type == "splat_type":
            name_node = next(
                (c for c in inner.named_children if c.type == "identifier"), None
            )
        elif inner.type == "constrained_type":
            type_children = [c for c in inner.named_children if c.type == "type"]
            if type_children:
                head = (
                    type_children[0].named_children[0]
                    if type_children[0].named_children
                    else None
                )
                if head is not None and head.type == "identifier":
                    name_node = head
                if len(type_children) > 1:
                    bound_node = type_children[1]
        if name_node is None:
            continue
        param_name = name_node.text.decode("utf-8")
        declared[param_name] = _declare_type_parameter(state, name_node, param_name)
        if bound_node is not None:
            bounds.append(bound_node)

    return declared, bounds


def _bind_generic_header(
    state: BinderState,
    type_params_node: Node,
    header_nodes: list[Node],
    reenter: bool = True,
) -> None:
    """Bind a PEP 695 generic definition's type parameters and its header.

    Call with the definition's own scope already pushed. The type parameters
    are declared *in* that scope — that is where their symbol ids come from
    and where the body, parameter annotations and nested scopes look them up.
    The header nodes (type-parameter bounds, plus the base-class list / return
    annotation / alias value) are then bound one scope out, with the type
    parameters overlaid, because PEP 695's implicit annotation scope is
    positioned between the definition and its parent rather than inside it:

    * it can see the immediately enclosing class namespace, which a nested
      scope could not — CPython resolves ``Alias`` in ``class C: Alias = int;
      def m[T](self) -> Alias: ...``; and
    * base-class references must land in the class's parent scope, which is
      how ``analysis/hierarchy.py`` tells a base apart from any other header
      reference.

    ``reenter=False`` leaves the scope popped, for a ``type X[T] = ...``
    statement, whose TYPE_PARAMS scope has no body to walk afterwards.
    """
    from pypeeker.binder.binder import visit_node

    type_params, bounds = _declare_type_parameters(state, type_params_node)
    entry = state.scope_stack.pop_entry()
    with state.scope_stack.type_param_overlay(type_params):
        for bound_node in bounds:
            visit_node(state, bound_node)
        for header_node in header_nodes:
            visit_node(state, header_node)
    if reenter:
        state.scope_stack.push_entry(entry)
