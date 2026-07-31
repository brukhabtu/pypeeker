"""Visitor functions for name uses (reads, calls, attribute access).

All functions take :class:`BinderState` as their first argument and append
to ``state.references``.
"""

from __future__ import annotations

import dataclasses

from tree_sitter import Node

from pypeeker.binder.helpers import (
    BUILTIN_NAMES,
    builtin_symbol_id,
    determine_attribute_ref_kind,
    determine_reference_kind,
    make_location,
    node_key,
)
from pypeeker.binder.imports import maybe_declare_dynamic_import
from pypeeker.binder.state import BinderState
from pypeeker.models import Reference, ReferenceKind


def _make_name_reference(
    state: BinderState, name: str, kind: ReferenceKind, node: Node
) -> Reference:
    """Resolve ``name`` against scope, then builtins, then fall back to unresolved.

    Centralises the resolution policy so every visitor that turns a bare
    identifier into a Reference reaches the same answer.
    """
    location = make_location(state.file_path, node)
    in_scope_id = state.scope_stack.current_scope.scope_id

    resolved = state.scope_stack.resolve(name)
    if resolved:
        return Reference(
            symbol_id=resolved.symbol_id,
            kind=kind,
            location=location,
            in_scope_id=in_scope_id,
        )
    if name in BUILTIN_NAMES:
        return Reference(
            symbol_id=builtin_symbol_id(name),
            kind=kind,
            location=location,
            in_scope_id=in_scope_id,
        )
    return Reference(
        symbol_id=name,
        kind=kind,
        location=location,
        in_scope_id=in_scope_id,
        resolved=False,
    )


_TRUTHINESS_PARENTS = frozenset({"if_statement", "while_statement", "elif_clause"})


def _is_membership_right(comparison: Node, child: Node) -> bool:
    """True when ``child`` is the right operand of an ``in`` / ``not in`` test.

    A membership test (``y in x``) only inspects ``x``'s contents, so holding a
    tuple there is equivalent to a list — but only for the *right* operand.
    ``x in y`` reads ``x`` as the left operand, whose membership in something
    else says nothing about ``x``'s own type, so that read still escapes; this
    function returns False for it. ``x == [..]`` (plain comparison) is likewise
    NOT safe — tuple and list compare unequal — so only the ``in`` operator,
    with ``child`` on the right, counts as local.
    """
    named = [c for c in comparison.children if c.is_named]
    if len(named) < 2 or child != named[-1]:
        return False
    return any(c.type in ("in", "not in") for c in comparison.children)


def _read_escapes(node: Node) -> bool:
    """Whether reading ``node`` lets its value escape or be type-inspected.

    Returns ``False`` only for the positions where substituting a tuple for a
    list is provably safe and non-retaining (see :attr:`Reference.escapes`);
    every other position — and anything unrecognised — counts as escaping, so
    the signal is conservative by construction.

    Classification is by the read's *immediate* syntactic role (after seeing
    through parentheses), not a walk to the enclosing statement, so a nested
    read is judged by what directly wraps it. The safe positions and the
    reasoning:

    * ``for _ in x`` / ``[_ for _ in x]`` — iteration reads elements only.
    * ``y in x`` / ``y not in x`` — membership inspects ``x``'s contents. Note
      the operand matters: in ``x in y`` it is ``y`` being inspected, so the
      read of ``x`` is the *left* operand and escapes (see
      :func:`_is_membership_right`).
    * ``x[i]`` — an element is taken; the container itself stays local. This
      holds even inside a return: in ``return x[i]`` the element escapes, not
      ``x``, so the read of ``x`` is local.
    * ``if x:`` / ``while x:`` / ``assert x`` / ``not x`` — truthiness is
      identical for a tuple and a list.

    Everything else escapes, including several cases that look harmless but are
    not — this is where the conservatism earns its keep::

        return x            # the whole list leaves the function
        f(x)                # a callee may mutate it (e.g. heapq.heappush) or
                            #   require a list — even len(x)/sorted(x) escape,
                            #   since this pass can't prove the callee is safe
        y = x               # aliased; y.append(...) would mutate the shared list
        x + other           # tuple + list raises TypeError
        x == other          # a tuple compares unequal to a list — result changes
        x.copy()            # attribute access: tuples lack .copy; even x.count(1)
                            #   reads x at the attribute position and escapes

    A read that reaches any of these makes tuplifying ``x`` potentially
    behavior-changing, so the binder marks it escaping and ``prefer-tuple``
    leaves the list alone.
    """
    child = node
    parent = node.parent
    # See through parentheses: ``(x)`` has the same role as ``x``.
    while parent is not None and parent.type == "parenthesized_expression":
        child, parent = parent, parent.parent
    if parent is None:
        return True
    ptype = parent.type

    # ``x[i]`` — element read; the container itself stays local.
    if ptype == "subscript" and child == parent.child_by_field_name("value"):
        return False
    # ``for _ in x`` (statement and comprehension) — iteration only.
    if ptype in ("for_statement", "for_in_clause") and child == parent.child_by_field_name(
        "right"
    ):
        return False
    # ``if x:`` / ``while x:`` / ``elif x:`` — truthiness only.
    if ptype in _TRUTHINESS_PARENTS and child == parent.child_by_field_name("condition"):
        return False
    # ``assert x`` / ``not x`` — truthiness only.
    if ptype in ("assert_statement", "not_operator"):
        return False
    # ``y in x`` / ``y not in x`` — membership inspects contents only.
    if ptype == "comparison_operator" and _is_membership_right(parent, child):
        return False
    return True


def visit_identifier(state: BinderState, node: Node) -> None:
    """Handle an identifier that is not in a declaration context."""
    if node_key(node) in state.declaration_nodes:
        return

    name = node.text.decode("utf-8")

    # Skip keywords that tree-sitter might parse as identifiers.
    if name in ("True", "False", "None"):
        return

    kind = determine_reference_kind(node)
    reference = _make_name_reference(state, name, kind, node)
    if kind is ReferenceKind.READ:
        reference = dataclasses.replace(reference, escapes=_read_escapes(node))
    state.references.append(reference)


def visit_keyword_argument(state: BinderState, node: Node) -> None:
    """Handle ``func(name=value)`` — the keyword name is syntax, not a reference.

    Mark the name identifier as a declaration so ``visit_identifier`` won't
    fire on it later; visit the value expression normally so any references
    inside it are recorded.
    """
    from pypeeker.binder.binder import visit_node

    name_node = node.child_by_field_name("name")
    if name_node is not None:
        state.declaration_nodes.add(node_key(name_node))

    value_node = node.child_by_field_name("value")
    if value_node is not None:
        visit_node(state, value_node)


def _call_result_discarded(call_node: Node | None) -> bool:
    """True when ``call_node``'s value is syntactically discarded.

    A call's result is discarded exactly when the call — or ``await <call>``
    — is itself the expression of a bare ``expression_statement`` (``f()`` or
    ``await f()`` on its own line). Anything else (assignment, return,
    argument position, comparison, receiver of a chained attribute, yield,
    tuple expression, ...) keeps the result "used". ``call_node`` must be the
    OUTERMOST ``call`` node of the reference: for ``a.b()`` that is the parent
    of the ``attribute`` node, not the attribute itself.
    """
    if call_node is None:
        return False
    parent = call_node.parent
    if parent is not None and parent.type == "await":
        parent = parent.parent
    return parent is not None and parent.type == "expression_statement"


def visit_call(state: BinderState, node: Node) -> None:
    """Handle function calls — the function name gets a CALL reference."""
    from pypeeker.binder.binder import visit_node

    # A dynamic import (importlib.import_module/__import__ with a literal path)
    # is also recorded as an IMPORT symbol for boundary enforcement, in
    # addition to the normal CALL reference on the callee below.
    maybe_declare_dynamic_import(state, node)

    function_node = node.child_by_field_name("function")
    args_node = node.child_by_field_name("arguments")

    if function_node:
        if function_node.type == "identifier":
            name = function_node.text.decode("utf-8")
            state.declaration_nodes.add(node_key(function_node))
            call_ref = _make_name_reference(
                state, name, ReferenceKind.CALL, function_node
            )
            if _call_result_discarded(node):
                call_ref = dataclasses.replace(call_ref, result_used=False)
            state.references.append(call_ref)
        elif function_node.type == "attribute":
            _visit_attribute_call(state, function_node)
        else:
            # Other complex expressions like foo()() — visit normally.
            visit_node(state, function_node)

    if args_node:
        # tree-sitter has two shapes for the ``arguments`` field:
        #   - ``argument_list`` for normal calls — iterate its children.
        #   - the bare comprehension / generator_expression node for
        #     ``func(x for x in xs)`` — visit it as one node so the
        #     comprehension scope is established before its body is bound.
        if args_node.type == "argument_list":
            for child in args_node.children:
                visit_node(state, child)
        else:
            visit_node(state, args_node)


def _visit_attribute_call(state: BinderState, attr_node: Node) -> None:
    """Handle attribute-based calls like ``self.method()`` or ``obj.func()``."""
    from pypeeker.binder.binder import visit_node

    object_node = attr_node.child_by_field_name("object")
    attribute_node = attr_node.child_by_field_name("attribute")

    if not object_node or not attribute_node:
        return

    state.declaration_nodes.add(node_key(attr_node))

    attr_name = attribute_node.text.decode("utf-8")
    receiver_root_id, receiver_chain = receiver_metadata(state, attr_node)
    # The outermost call node for ``a.b()`` is the *parent* of the attribute.
    result_used = not _call_result_discarded(attr_node.parent)

    if object_node.type == "identifier":
        obj_name = object_node.text.decode("utf-8")

        state.declaration_nodes.add(node_key(object_node))
        state.references.append(
            _make_name_reference(state, obj_name, ReferenceKind.READ, object_node)
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
                    result_used=result_used,
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
            result_used=result_used,
        )
    )


def visit_attribute(state: BinderState, node: Node) -> None:
    """Handle non-call attribute access like ``self.x`` or ``obj.y``."""
    from pypeeker.binder.binder import visit_node

    if node_key(node) in state.declaration_nodes:
        return

    object_node = node.child_by_field_name("object")
    attribute_node = node.child_by_field_name("attribute")
    if not object_node or not attribute_node:
        return

    state.declaration_nodes.add(node_key(node))
    attr_name = attribute_node.text.decode("utf-8")

    ref_kind = determine_attribute_ref_kind(node)
    receiver_root_id, receiver_chain = receiver_metadata(state, node)

    if object_node.type == "identifier":
        obj_name = object_node.text.decode("utf-8")
        state.declaration_nodes.add(node_key(object_node))
        state.references.append(
            _make_name_reference(state, obj_name, ReferenceKind.READ, object_node)
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
