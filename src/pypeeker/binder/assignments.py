"""Visitor functions for assignment-like constructs and variable declarations.

Covers ``=``, augmented assignment, walrus, ``for`` loop targets, ``with``
items, ``except`` clause targets, and the lower-level
:func:`declare_variable` / :func:`make_variable_symbol` helpers.
"""

from __future__ import annotations

from tree_sitter import Node

from pypeeker.binder.helpers import (
    build_symbol_id_for_scope,
    node_key,
    extract_targets,
    make_location,
)
from pypeeker.binder.state import BinderState
from pypeeker.models import (
    Confidence,
    Reference,
    ReferenceKind,
    ScopeKind,
    Symbol,
    SymbolKind,
    TypeAnnotation,
)


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
        # With no explicit annotation, infer a type from a constructor call on
        # the RHS (``x = Foo()`` / ``self.x = Foo()``). Recorded at INFERRED
        # confidence; resolution only succeeds if the name turns out to be a
        # class with the accessed member, so over-recording is harmless.
        if type_ann is None and (len(targets) == 1 or _self_attribute_target(left)):
            inferred = _constructor_type_name(right) or _literal_list_type(right)
            if inferred is not None:
                type_ann = TypeAnnotation(raw=inferred, confidence=Confidence.INFERRED)
        if targets:
            for target_node in targets:
                name = target_node.text.decode("utf-8")
                declare_variable(state, target_node, name, type_ann)
        else:
            # ``self.x = ...`` / ``cls.x = ...`` declares an instance attribute
            # as a member of the enclosing class, so ``obj.x`` / ``self.x.y()``
            # can resolve through its type.
            attr_node = _self_attribute_target(left)
            if attr_node is not None:
                _declare_instance_attribute(
                    state, attr_node.text.decode("utf-8"), attr_node, type_ann
                )
            elif left is not None and left.type == "subscript":
                # ``x[i] = v`` mutates x — record it as a WRITE of the root.
                _record_subscript_mutation(state, left)
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


def _literal_list_type(node: Node | None) -> str | None:
    """Return ``"list"`` if ``node`` is a list literal or comprehension."""
    if node is not None and node.type in ("list", "list_comprehension"):
        return "list"
    return None


def _subscript_root_node(node: Node | None) -> Node | None:
    """Return the root value node of a subscript target.

    Walks nested subscripts: ``x[i][j]`` -> ``x``; ``obj.attr[i][j]`` ->
    the ``obj.attr`` attribute node. Whatever non-subscript node anchors
    the chain is returned (identifier, attribute, call, ...).
    """
    current = node
    while current is not None and current.type == "subscript":
        current = current.child_by_field_name("value")
    return current


def _record_subscript_mutation(state: BinderState, subscript_node: Node) -> None:
    """Record the root of a subscript assignment target as a WRITE (mutation).

    ``x[i] = v`` mutates ``x``; ``obj.attr[k] = v`` mutates ``obj.attr``.
    The binder otherwise records these roots as reads, so the root is marked
    as handled and a WRITE reference is emitted instead. Dynamic roots
    (``f()[k] = v``) record no mutation fact.
    """
    root = _subscript_root_node(subscript_node)
    if root is None:
        return
    if root.type == "identifier":
        state.declaration_nodes.add(node_key(root))
        name = root.text.decode("utf-8")
        resolved = state.scope_stack.resolve(name)
        state.references.append(
            Reference(
                symbol_id=resolved.symbol_id if resolved else name,
                kind=ReferenceKind.WRITE,
                location=make_location(state.file_path, root),
                in_scope_id=state.scope_stack.current_scope.scope_id,
                resolved=resolved is not None,
            )
        )
    elif root.type == "attribute":
        _record_attribute_subscript_mutation(state, root)


def _record_attribute_subscript_mutation(state: BinderState, attr_node: Node) -> None:
    """Record ``obj.attr[k] = v`` as a WRITE of the ``obj.attr`` chain.

    Mirrors :func:`pypeeker.binder.references.visit_attribute` for the
    attribute-write case (``a.b = x``) — same symbol_id shape, same READ on
    the receiver-root identifier, same receiver metadata — but forces
    kind=WRITE, since ``determine_attribute_ref_kind`` only inspects the
    attribute node's direct parent (here a subscript) and would say READ.
    """
    import dataclasses

    # Local import: assignments must not import references at module level
    # to keep the binder package's import graph one-directional in spirit;
    # binder.binder imports both, so this is always available at call time.
    from pypeeker.binder.binder import visit_node
    from pypeeker.binder.references import (
        _make_name_reference,
        receiver_metadata,
        resolve_self_attribute,
    )

    object_node = attr_node.child_by_field_name("object")
    attribute_node = attr_node.child_by_field_name("attribute")
    if not object_node or not attribute_node:
        return

    # Mark the attribute node so visit_attribute (reached when the assignment
    # target is later visited) does not also emit a READ for the same chain.
    state.declaration_nodes.add(node_key(attr_node))
    attr_name = attribute_node.text.decode("utf-8")
    receiver_root_id, receiver_chain = receiver_metadata(state, attr_node)

    if object_node.type == "identifier":
        obj_name = object_node.text.decode("utf-8")
        state.declaration_nodes.add(node_key(object_node))
        # The receiver root is still read (matches visit_attribute on
        # ``a.b = x``, which records a READ of ``a``).
        state.references.append(
            _make_name_reference(state, obj_name, ReferenceKind.READ, object_node)
        )

        if obj_name in ("self", "cls"):
            ref = resolve_self_attribute(
                state, attr_name, attribute_node, ReferenceKind.WRITE
            )
            if ref:
                state.references.append(
                    dataclasses.replace(
                        ref,
                        receiver_root_symbol_id=receiver_root_id,
                        receiver_chain=receiver_chain,
                    )
                )
                return
    else:
        visit_node(state, object_node)

    state.references.append(
        Reference(
            symbol_id=f"<unresolved>.{attr_name}",
            kind=ReferenceKind.WRITE,
            location=make_location(state.file_path, attribute_node),
            in_scope_id=state.scope_stack.current_scope.scope_id,
            resolved=False,
            is_attribute_access=True,
            receiver_root_symbol_id=receiver_root_id,
            receiver_chain=receiver_chain,
        )
    )


def _self_attribute_target(node: Node | None) -> Node | None:
    """Return the attribute-name node if ``node`` is ``self.x`` / ``cls.x``.

    None for any other target shape. Used to recognize instance-attribute
    assignments.
    """
    if node is None or node.type != "attribute":
        return None
    obj = node.child_by_field_name("object")
    attr = node.child_by_field_name("attribute")
    if (
        obj is not None
        and obj.type == "identifier"
        and obj.text.decode("utf-8") in ("self", "cls")
        and attr is not None
    ):
        return attr
    return None


def _declare_instance_attribute(
    state: BinderState,
    name: str,
    node: Node,
    type_ann: TypeAnnotation | None = None,
) -> None:
    """Declare ``self.<name>`` as a member of the enclosing class.

    Skipped when the class already declares ``name`` (a class-level field or an
    earlier ``self.<name>`` assignment), so the first/most-specific declaration
    wins and no duplicate member is created.
    """
    class_scope = state.scope_stack.find_enclosing_class()
    if class_scope is None:
        return
    class_entry = state.scope_stack.get_class_scope_entry(class_scope.scope_id)
    if class_entry is None or class_entry.lookup_local(name) is not None:
        return
    symbol_id = build_symbol_id_for_scope(class_scope, name, state.module_path)
    symbol = _make_variable_symbol(state, node, name, symbol_id, type_ann)
    symbol.parent_scope_id = class_scope.scope_id
    state.scope_stack.declare_in_scope(name, symbol, class_entry)
    state.symbols.append(symbol)
    class_scope.symbol_ids.append(symbol.symbol_id)


def visit_augmented_assignment(state: BinderState, node: Node) -> None:
    """Handle ``x += expr``. Read+write, not a new declaration."""
    from pypeeker.binder.binder import visit_node

    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")

    if left and left.type == "identifier":
        name = left.text.decode("utf-8")
        state.declaration_nodes.add(node_key(left))
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
    elif left is not None and left.type == "subscript":
        # ``x[i] += v`` mutates x; record the write, then visit the index.
        _record_subscript_mutation(state, left)
        visit_node(state, left)

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
        state.declaration_nodes.add(node_key(name_node))

        current_kind = state.scope_stack.current_scope.kind
        if current_kind == ScopeKind.COMPREHENSION:
            target_entry = state.scope_stack.find_enclosing_function_entry()
            if target_entry:
                symbol_id = build_symbol_id_for_scope(
                    target_entry.scope, name, state.module_path
                )
                symbol = _make_variable_symbol(state, name_node, name, symbol_id)
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
                    _visit_with_item(state, with_item)

    if body:
        for child in body.children:
            visit_node(state, child)


def _visit_with_item(state: BinderState, node: Node) -> None:
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
    state.declaration_nodes.add(node_key(node))
    current_entry = state.scope_stack.current

    # Check global/nonlocal redirects.
    if name in current_entry.globals_declared:
        target_entry = state.scope_stack.find_global_target()
        symbol_id = build_symbol_id_for_scope(
            target_entry.scope, name, state.module_path
        )
        symbol = _make_variable_symbol(state, node, name, symbol_id, type_ann)
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
            symbol = _make_variable_symbol(state, node, name, symbol_id, type_ann)
            symbol.parent_scope_id = target_entry.scope.scope_id
            state.scope_stack.declare_in_scope(name, symbol, target_entry)
            state.symbols.append(symbol)
            target_entry.scope.symbol_ids.append(symbol.symbol_id)
            return

    scope = state.scope_stack.current_scope
    symbol_id = state.scope_stack.build_symbol_id(state.module_path, name)
    symbol = _make_variable_symbol(state, node, name, symbol_id, type_ann)
    symbol.parent_scope_id = scope.scope_id
    final_id = state.scope_stack.declare(name, symbol)
    state.symbols.append(symbol)
    scope.symbol_ids.append(final_id)


def _make_variable_symbol(
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
