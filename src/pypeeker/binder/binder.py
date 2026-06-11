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
from pypeeker.binder.helpers import (
    compute_hash,
    make_location,
    make_span,
    node_key,
)
from pypeeker.paths import module_path_from
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
    visit_keyword_argument,
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
from pypeeker.models.symbols import Symbol, SymbolKind


def bind(
    adapter: PythonAdapter,
    file_path: str,
    source: bytes,
    root: Node,
    module_path: str | None = None,
) -> FileIndex:
    """Walk the CST and produce a FileIndex.

    The public entry point. Builds a :class:`BinderState`, runs the visitor
    dispatch from the module root, and assembles the result.

    ``module_path`` is the dotted semantic path that roots every symbol_id;
    when omitted it's derived from ``file_path`` (no src-root stripping),
    which suits inline/test sources. The indexer passes the project-aware
    module path explicitly.
    """
    if module_path is None:
        module_path = module_path_from(file_path)
    state = BinderState(
        adapter=adapter, file_path=file_path, module_path=module_path, source=source
    )
    state.errors.extend(_collect_syntax_errors(root))
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


def _collect_syntax_errors(root: Node) -> list[str]:
    """Collect concise syntax-error entries from a parse tree.

    tree-sitter recovers from malformed input by emitting ``ERROR`` nodes
    (unparseable stretches) and *missing* nodes (tokens inserted to complete
    a rule). Recording them in :attr:`FileIndex.errors` makes a partially
    bound index visibly partial instead of silently incomplete.

    The walk is cheap: ``root.has_error`` short-circuits clean trees, and
    descent is pruned to subtrees that actually contain errors. One entry is
    emitted per ``ERROR`` node (without descending into it — nested errors
    inside an unparseable stretch are noise) and per missing token.
    """
    if not root.has_error:
        return []
    errors: list[str] = []
    stack = [root]
    while stack:
        node = stack.pop()
        line = node.start_point[0] + 1
        column = node.start_point[1] + 1
        if node.type == "ERROR":
            errors.append(f"syntax error at line {line}, column {column}")
            continue
        if node.is_missing:
            errors.append(f"missing {node.type} at line {line}, column {column}")
            continue
        # Push in reverse so entries come out in document order.
        for child in reversed(node.children):
            if child.has_error or child.is_missing:
                stack.append(child)
    return errors


def visit_module(state: BinderState, node: Node) -> None:
    """Bind the module-level scope, walk children, then fix up forward refs."""
    scope = Scope(
        scope_id=state.module_path,
        name=state.module_path,
        kind=ScopeKind.MODULE,
        file_path=state.file_path,
        span=make_span(node),
    )
    state.scopes.append(scope)
    state.scope_stack.push(scope)
    for child in node.children:
        visit_node(state, child)
    state.scope_stack.pop()
    _resolve_module_forward_refs(state)
    _emit_module_symbol(state, node)


def _emit_module_symbol(state: BinderState, node: Node) -> None:
    """Add the module itself as a first-class MODULE symbol.

    Its ``symbol_id`` equals the module scope id (the dotted module path), so it
    threads cleanly into the cross-file tree, where its package parent is set.
    ``parent_scope_id`` stays ``None`` — packages are not scopes — and the
    symbol is deliberately kept out of the module scope's ``symbol_ids`` so
    name-resolution and visible-symbol queries are unaffected.
    """
    if not state.module_path:
        return
    name = state.module_path.rsplit(".", 1)[-1]
    visibility, vis_confidence = state.adapter.get_visibility(name)
    state.symbols.append(
        Symbol(
            symbol_id=state.module_path,
            name=name,
            kind=SymbolKind.MODULE,
            location=make_location(state.file_path, node),
            visibility=visibility,
            visibility_confidence=vis_confidence,
            docstring=_module_docstring(node),
        )
    )


def _module_docstring(node: Node) -> str | None:
    """Return the module-level docstring, if the first statement is a string."""
    for child in node.children:
        if child.type == "comment":
            continue
        if child.type == "expression_statement" and child.children:
            string_node = child.children[0]
            if string_node.type == "string":
                text = string_node.text.decode("utf-8")
                if text.startswith(('"""', "'''")):
                    return text[3:-3].strip()
                if text.startswith(('"', "'")):
                    return text[1:-1].strip()
        return None
    return None


def _resolve_module_forward_refs(state: BinderState) -> None:
    """Re-bind unresolved bare-name references against the module scope.

    Python is effectively two-pass at module level: every top-level ``def`` /
    ``class`` / assignment registers its name before any function body runs.
    The binder walks once top-to-bottom, so a function body that calls a
    helper defined later in the same file produced an unresolved reference.

    Once the module is fully walked, every module-level symbol is in
    ``state.symbols``. We make one final pass: any unresolved reference whose
    bare ``symbol_id`` matches a module-level symbol is updated in place.
    Builtins (``<builtins>.X``), already-resolved refs, and qualified ids
    (``file.py:Foo.bar``) are skipped.
    """
    import dataclasses

    module_symbols: dict[str, Symbol] = {}
    for symbol in state.symbols:
        if symbol.parent_scope_id == state.module_path:
            module_symbols[symbol.name] = symbol
    if not module_symbols:
        return

    builtins_prefix = "<builtins>."
    for i, ref in enumerate(state.references):
        sid = ref.symbol_id
        if sid.startswith(builtins_prefix):
            # A builtin was used at a site where the module hadn't yet
            # declared its shadowing name. Re-bind if the module did
            # declare one by end-of-file.
            name = sid[len(builtins_prefix):]
        elif not ref.resolved and ":" not in sid and not sid.startswith("<"):
            name = sid
        else:
            continue
        target = module_symbols.get(name)
        if target is None:
            continue
        state.references[i] = dataclasses.replace(
            ref, symbol_id=target.symbol_id, resolved=True
        )


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
    elif node_type in ("import_from_statement", "future_import_statement"):
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
    elif node_type == "identifier" and node_key(node) not in state.declaration_nodes:
        visit_identifier(state, node)
    elif node_type == "call":
        visit_call(state, node)
    elif node_type == "keyword_argument":
        visit_keyword_argument(state, node)
    elif node_type == "attribute":
        visit_attribute(state, node)
    else:
        for child in node.children:
            visit_node(state, child)
