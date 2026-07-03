"""Visitor functions for import statements + global/nonlocal declarations.

All functions take :class:`BinderState` as their first argument and mutate
it to record symbols and references.
"""

from __future__ import annotations

from tree_sitter import Node

from pypeeker.binder.helpers import make_location, node_key, resolve_relative_import
from pypeeker.binder.state import BinderState
from pypeeker.models.capabilities import Confidence
from pypeeker.models.symbols import Symbol, SymbolKind, Visibility

# Callables whose string-literal first argument names a module imported at
# runtime. ``importlib.import_module`` is matched by its attribute name (any
# receiver); ``__import__`` is the builtin form.
_DYNAMIC_IMPORT_ATTRS: frozenset[str] = frozenset({"import_module"})
_DYNAMIC_IMPORT_NAMES: frozenset[str] = frozenset({"__import__"})


def visit_import_statement(state: BinderState, node: Node) -> None:
    """Handle ``import x`` and ``import x as y``."""
    for child in node.children:
        if child.type == "dotted_name":
            name = child.text.decode("utf-8")
            _declare_import(state, child, name, name)
        elif child.type == "aliased_import":
            module_node = child.child_by_field_name("name")
            alias_node = child.child_by_field_name("alias")
            if module_node and alias_node:
                _declare_import(
                    state,
                    alias_node,
                    alias_node.text.decode("utf-8"),
                    module_node.text.decode("utf-8"),
                    imported_name_node=module_node,
                )
            elif module_node:
                name = module_node.text.decode("utf-8")
                _declare_import(state, module_node, name, name)


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

    # Relative imports resolve against the dotted module path (src-stripped),
    # not the physical file path — see resolve_relative_import. An __init__.py
    # is the package itself: its module_path already names the containing
    # package, which shifts how many segments each leading dot strips.
    is_package = (
        state.file_path.replace("\\", "/").rsplit("/", 1)[-1] == "__init__.py"
    )
    module_name = resolve_relative_import(
        state.module_path, module_name, is_package=is_package
    )

    for child in node.children:
        if child.type == "dotted_name" and child != module_node:
            name = child.text.decode("utf-8")
            _declare_import(state, child, name, f"{module_name}.{name}")
        elif child.type == "aliased_import":
            import_name_node = child.child_by_field_name("name")
            alias_node = child.child_by_field_name("alias")
            if import_name_node and alias_node:
                _declare_import(
                    state,
                    alias_node,
                    alias_node.text.decode("utf-8"),
                    f"{module_name}.{import_name_node.text.decode('utf-8')}",
                    imported_name_node=import_name_node,
                )
            elif import_name_node:
                name = import_name_node.text.decode("utf-8")
                _declare_import(
                    state, import_name_node, name, f"{module_name}.{name}"
                )
        elif child.type == "identifier" and child != module_node:
            # Direct identifier import (e.g., ``from os import path``)
            if child.prev_sibling and child.prev_sibling.type == "import":
                name = child.text.decode("utf-8")
                _declare_import(state, child, name, f"{module_name}.{name}")
        elif child.type == "wildcard_import":
            # ``from m import *`` — record the star itself as an IMPORT
            # symbol bound to the local name "*", with ``imported_from``
            # naming the (relative-resolved) module rather than a
            # ``module.name`` path: the star covers the module's whole
            # public surface. Declaring "*" through the normal path is
            # inert for name resolution (no identifier is ever spelled
            # ``*``), so cross-module consumers get the fact without
            # changing how the names the star supplies bind — they stay
            # unresolved bare references, attributed by the star-imports
            # rule's cross-module resolution.
            _declare_import(state, child, "*", module_name)


def _declare_import(
    state: BinderState,
    node: Node,
    local_name: str,
    module_path: str,
    imported_name_node: Node | None = None,
) -> None:
    """Record an IMPORT symbol in the current scope."""
    state.declaration_nodes.add(node_key(node))
    scope = state.scope_stack.current_scope
    visibility, vis_confidence = state.adapter.get_visibility(local_name)
    symbol_id = state.scope_stack.build_symbol_id(state.module_path, local_name)

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


def maybe_declare_dynamic_import(state: BinderState, call_node: Node) -> None:
    """Record ``importlib.import_module("x")`` / ``__import__("x")`` as an IMPORT.

    A dynamic import is invisible to boundary enforcement because the target is
    a runtime string, not an ``import`` statement. When the first positional
    argument is a plain string literal we recover the module path and declare a
    synthetic IMPORT symbol carrying ``imported_from`` (the literal path) at
    :attr:`Confidence.HEURISTIC` — the target is known but the binding is
    best-effort. Non-literal arguments (variables, f-strings with substitution,
    concatenations) name no static module and are ignored, as is
    ``import_module`` on any receiver other than the ``importlib`` module
    itself (an unrelated method of the same name is not an import).

    The symbol is appended to ``state.symbols`` but *not* declared into any
    scope: a dynamic import binds no in-scope name, so it must not shadow real
    names or participate in reference resolution — it exists only as a fact for
    the ``import-boundaries`` rule.
    """
    function_node = call_node.child_by_field_name("function")
    if function_node is None:
        return
    if function_node.type == "identifier":
        if function_node.text.decode("utf-8") not in _DYNAMIC_IMPORT_NAMES:
            return
    elif function_node.type == "attribute":
        attr_node = function_node.child_by_field_name("attribute")
        if attr_node is None or (
            attr_node.text.decode("utf-8") not in _DYNAMIC_IMPORT_ATTRS
        ):
            return
        # Only the stdlib entry point: any object may have a method named
        # `import_module` (a plugin registry, a loader), and treating those as
        # imports would fabricate boundary edges from a name collision.
        obj_node = function_node.child_by_field_name("object")
        if obj_node is None or obj_node.type != "identifier":
            return
        if obj_node.text.decode("utf-8") != "importlib":
            return
    else:
        return

    module_path = _first_string_literal(call_node)
    if not module_path:
        return

    scope = state.scope_stack.current_scope
    symbol = Symbol(
        symbol_id=f"{state.module_path}:<dynamic-import@{call_node.start_byte}>",
        name="<dynamic-import>",
        kind=SymbolKind.IMPORT,
        location=make_location(state.file_path, call_node),
        # Not an export: the call binds no module-level name, so visibility
        # rules must never treat it as public API surface.
        visibility=Visibility.PRIVATE,
        visibility_confidence=Confidence.HEURISTIC,
        parent_scope_id=scope.scope_id,
        imported_from=module_path,
        import_confidence=Confidence.HEURISTIC,
    )
    state.symbols.append(symbol)


def _first_string_literal(call_node: Node) -> str | None:
    """The value of a call's first positional argument when it is a plain string.

    Returns ``None`` unless the first argument is a single string literal with
    no interpolation (an f-string like ``f"{pkg}.mod"`` is not literal). The
    surrounding quotes and any prefix (``r``/``b``) are stripped by reading the
    ``string_content`` child(ren).
    """
    args_node = call_node.child_by_field_name("arguments")
    if args_node is None or args_node.type != "argument_list":
        return None
    first = next((c for c in args_node.named_children), None)
    if first is None or first.type != "string":
        return None
    parts: list[str] = []
    for child in first.children:
        if child.type == "interpolation":
            return None  # f-string with substitution — not a static path
        if child.type == "string_content":
            parts.append(child.text.decode("utf-8"))
    return "".join(parts)


def visit_global_statement(state: BinderState, node: Node) -> None:
    """Record names declared ``global`` so later assignments redirect to module scope."""
    for child in node.children:
        if child.type == "identifier":
            name = child.text.decode("utf-8")
            state.scope_stack.current.globals_declared.add(name)
            state.declaration_nodes.add(node_key(child))


def visit_nonlocal_statement(state: BinderState, node: Node) -> None:
    """Record names declared ``nonlocal`` so later assignments redirect to the enclosing scope."""
    for child in node.children:
        if child.type == "identifier":
            name = child.text.decode("utf-8")
            state.scope_stack.current.nonlocals_declared.add(name)
            state.declaration_nodes.add(node_key(child))
