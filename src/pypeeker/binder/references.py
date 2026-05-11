"""Visitor functions for name uses (reads, calls, attribute access).

All functions take :class:`BinderState` as their first argument and append
to ``state.references``.
"""

from __future__ import annotations

import dataclasses

from tree_sitter import Node

from pypeeker.binder.helpers import (
    determine_attribute_ref_kind,
    determine_reference_kind,
    make_location,
)
from pypeeker.binder.state import BinderState
from pypeeker.models.references import Reference, ReferenceKind


def visit_identifier(state: BinderState, node: Node) -> None:
    """Handle an identifier that is not in a declaration context."""
    if id(node) in state.declaration_nodes:
        return

    name = node.text.decode("utf-8")

    # Skip keywords that tree-sitter might parse as identifiers.
    if name in ("True", "False", "None"):
        return

    resolved = state.scope_stack.resolve(name)
    ref_kind = determine_reference_kind(node)

    if resolved:
        state.references.append(
            Reference(
                symbol_id=resolved.symbol_id,
                kind=ref_kind,
                location=make_location(state.file_path, node),
                in_scope_id=state.scope_stack.current_scope.scope_id,
            )
        )
    else:
        state.references.append(
            Reference(
                symbol_id=name,
                kind=ref_kind,
                location=make_location(state.file_path, node),
                in_scope_id=state.scope_stack.current_scope.scope_id,
                resolved=False,
            )
        )


def visit_call(state: BinderState, node: Node) -> None:
    """Handle function calls — the function name gets a CALL reference."""
    from pypeeker.binder.binder import visit_node

    function_node = node.child_by_field_name("function")
    args_node = node.child_by_field_name("arguments")

    if function_node:
        if function_node.type == "identifier":
            name = function_node.text.decode("utf-8")
            state.declaration_nodes.add(id(function_node))
            resolved = state.scope_stack.resolve(name)
            if resolved:
                state.references.append(
                    Reference(
                        symbol_id=resolved.symbol_id,
                        kind=ReferenceKind.CALL,
                        location=make_location(state.file_path, function_node),
                        in_scope_id=state.scope_stack.current_scope.scope_id,
                    )
                )
            else:
                state.references.append(
                    Reference(
                        symbol_id=name,
                        kind=ReferenceKind.CALL,
                        location=make_location(state.file_path, function_node),
                        in_scope_id=state.scope_stack.current_scope.scope_id,
                        resolved=False,
                    )
                )
        elif function_node.type == "attribute":
            visit_attribute_call(state, function_node)
        else:
            # Other complex expressions like foo()() — visit normally.
            visit_node(state, function_node)

    if args_node:
        for child in args_node.children:
            visit_node(state, child)


def visit_attribute_call(state: BinderState, attr_node: Node) -> None:
    """Handle attribute-based calls like ``self.method()`` or ``obj.func()``."""
    from pypeeker.binder.binder import visit_node

    object_node = attr_node.child_by_field_name("object")
    attribute_node = attr_node.child_by_field_name("attribute")

    if not object_node or not attribute_node:
        return

    state.declaration_nodes.add(id(attr_node))

    attr_name = attribute_node.text.decode("utf-8")
    receiver_root_id, receiver_chain = receiver_metadata(state, attr_node)

    if object_node.type == "identifier":
        obj_name = object_node.text.decode("utf-8")

        state.declaration_nodes.add(id(object_node))
        obj_resolved = state.scope_stack.resolve(obj_name)
        if obj_resolved:
            state.references.append(
                Reference(
                    symbol_id=obj_resolved.symbol_id,
                    kind=ReferenceKind.READ,
                    location=make_location(state.file_path, object_node),
                    in_scope_id=state.scope_stack.current_scope.scope_id,
                )
            )
        else:
            state.references.append(
                Reference(
                    symbol_id=obj_name,
                    kind=ReferenceKind.READ,
                    location=make_location(state.file_path, object_node),
                    in_scope_id=state.scope_stack.current_scope.scope_id,
                    resolved=False,
                )
            )

        if obj_name in ("self", "cls"):
            method_ref = resolve_self_attribute(
                state, attr_name, attribute_node, ReferenceKind.CALL
            )
            if method_ref:
                method_ref = dataclasses.replace(
                    method_ref,
                    receiver_root_symbol_id=receiver_root_id,
                    receiver_chain=receiver_chain,
                )
                state.references.append(method_ref)
                return

    else:
        visit_node(state, object_node)

    state.references.append(
        Reference(
            symbol_id=f"<unresolved>.{attr_name}",
            kind=ReferenceKind.CALL,
            location=make_location(state.file_path, attribute_node),
            in_scope_id=state.scope_stack.current_scope.scope_id,
            resolved=False,
            is_attribute_access=True,
            receiver_root_symbol_id=receiver_root_id,
            receiver_chain=receiver_chain,
        )
    )


def visit_attribute(state: BinderState, node: Node) -> None:
    """Handle non-call attribute access like ``self.x`` or ``obj.y``."""
    from pypeeker.binder.binder import visit_node

    if id(node) in state.declaration_nodes:
        return

    object_node = node.child_by_field_name("object")
    attribute_node = node.child_by_field_name("attribute")
    if not object_node or not attribute_node:
        return

    state.declaration_nodes.add(id(node))
    attr_name = attribute_node.text.decode("utf-8")

    ref_kind = determine_attribute_ref_kind(node)
    receiver_root_id, receiver_chain = receiver_metadata(state, node)

    if object_node.type == "identifier":
        obj_name = object_node.text.decode("utf-8")
        state.declaration_nodes.add(id(object_node))

        obj_resolved = state.scope_stack.resolve(obj_name)
        if obj_resolved:
            state.references.append(
                Reference(
                    symbol_id=obj_resolved.symbol_id,
                    kind=ReferenceKind.READ,
                    location=make_location(state.file_path, object_node),
                    in_scope_id=state.scope_stack.current_scope.scope_id,
                )
            )
        else:
            state.references.append(
                Reference(
                    symbol_id=obj_name,
                    kind=ReferenceKind.READ,
                    location=make_location(state.file_path, object_node),
                    in_scope_id=state.scope_stack.current_scope.scope_id,
                    resolved=False,
                )
            )

        if obj_name in ("self", "cls"):
            ref = resolve_self_attribute(state, attr_name, attribute_node, ref_kind)
            if ref:
                ref = dataclasses.replace(
                    ref,
                    receiver_root_symbol_id=receiver_root_id,
                    receiver_chain=receiver_chain,
                )
                state.references.append(ref)
                return
    else:
        visit_node(state, object_node)

    state.references.append(
        Reference(
            symbol_id=f"<unresolved>.{attr_name}",
            kind=ref_kind,
            location=make_location(state.file_path, attribute_node),
            in_scope_id=state.scope_stack.current_scope.scope_id,
            resolved=False,
            is_attribute_access=True,
            receiver_root_symbol_id=receiver_root_id,
            receiver_chain=receiver_chain,
        )
    )


def receiver_metadata(
    state: BinderState, attr_node: Node
) -> tuple[str | None, list[str] | None]:
    """Walk left from an attribute node to find the receiver root.

    For ``a.b.c``: returns (resolved_symbol_id_of_a, ['a', 'b']).
    For ``f().bar``: chain is broken by the call — returns (None, None).
    For ``unknown.bar`` where ``unknown`` is not in scope: returns (None, ['unknown']).
    """
    intermediate: list[str] = []
    current = attr_node.child_by_field_name("object")
    while current is not None:
        if current.type == "identifier":
            root_name = current.text.decode("utf-8")
            chain = [root_name] + list(reversed(intermediate))
            resolved = state.scope_stack.resolve(root_name)
            root_id = resolved.symbol_id if resolved else None
            return root_id, chain
        if current.type == "attribute":
            attr_name_node = current.child_by_field_name("attribute")
            if attr_name_node is None:
                return None, None
            intermediate.append(attr_name_node.text.decode("utf-8"))
            current = current.child_by_field_name("object")
            continue
        # Anything else (call, subscript, parenthesized expr, ...) is dynamic.
        return None, None
    return None, None


def resolve_self_attribute(
    state: BinderState,
    attr_name: str,
    attr_node: Node,
    kind: ReferenceKind,
) -> Reference | None:
    """Try to resolve ``self.attr`` or ``cls.attr`` to a class member."""
    class_scope = state.scope_stack.find_enclosing_class()
    if not class_scope:
        return None

    class_entry = state.scope_stack.get_class_scope_entry(class_scope.scope_id)
    if class_entry:
        symbol = class_entry.lookup_local(attr_name)
        if symbol:
            return Reference(
                symbol_id=symbol.symbol_id,
                kind=kind,
                location=make_location(state.file_path, attr_node),
                in_scope_id=state.scope_stack.current_scope.scope_id,
                is_attribute_access=True,
            )
    return None
