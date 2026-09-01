"""Call graph and reachability queries.

Builds a global ``{caller_function_id -> set[callee_function_id]}`` map
from every indexed file. Used by :mod:`pypeeker.analysis.purity`'s
transitive variant to flag functions that are pure in their own body but
call something impure.

Limitations of this v1 implementation, surfaced explicitly so callers
know what they get:

* Only resolved CALL references are followed. ``self._store.save()`` has
  an unresolved leaf because pypeeker doesn't resolve attribute methods
  through field types — those edges are invisible. Closing this requires
  combining typed-receiver resolution with cross-file lookup.
* Class constructor calls (``IndexStore(root)``) target a CLASS symbol,
  not its ``__init__``. Classes are treated as opaque in v1.
* First-class function passing / callbacks are not analyzed.

Within these limits, direct module-rooted calls (``helper()``,
``other_module.func()``, ``from lib import f; f()``) are caught precisely,
including calls routed through ``__init__.py`` barrel re-exports.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from pypeeker.models import ReferenceKind, SymbolKind
from pypeeker.query import SemanticQueryEngine
from pypeeker.storage import IndexStoreLike


@dataclass(frozen=True)
class TransitiveImpureCall:
    """A direct call to another function that's been classified as impure."""

    callee: str
    """``symbol_id`` of the impure callee."""
    line: int | None = None


def call_graph(
    store: IndexStoreLike, *, engine: SemanticQueryEngine | None = None
) -> dict[str, frozenset[str]]:
    """Return ``{caller_function_id -> frozenset[callee_function_id]}``.

    Both caller and callee must be FUNCTION or METHOD symbols. Module-level
    code, class bodies, and class symbols are not represented as callers
    or callees in v1.

    A call reference binds to a local symbol — the function itself for
    same-module calls, or an IMPORT symbol for cross-file calls. The shared
    :class:`~pypeeker.resolve.CrossModuleResolver` maps either to the canonical
    definition, following ``__init__.py`` barrel re-exports, so a call reached
    through a package barrel resolves to the same function id as a direct call.

    The indexes and the resolver come from ``engine``'s snapshot
    (:meth:`~pypeeker.query.SemanticQueryEngine.all_indexes` /
    :meth:`~pypeeker.query.SemanticQueryEngine.resolver`) rather than being
    loaded and built here a second time; pass the engine the caller already
    holds so a transitive walk shares one resolver. When omitted, a fresh
    engine is built over ``store``.
    """
    if engine is None:
        engine = SemanticQueryEngine(store)
    indexes = engine.all_indexes()

    function_ids: set[str] = {
        s.symbol_id
        for index in indexes
        for s in index.symbols
        if s.kind in (SymbolKind.FUNCTION, SymbolKind.METHOD)
    }
    resolver = engine.resolver()

    edges: dict[str, set[str]] = defaultdict(set)
    for index in indexes:
        for ref in index.references:
            if ref.kind != ReferenceKind.CALL:
                continue
            # Note: attribute/method calls are recorded unresolved by the binder
            # but may resolve via the receiver; rely on function_ids membership
            # to filter rather than ref.resolved.
            callee = resolver.resolve_reference(ref)
            caller = ref.in_scope_id
            if callee not in function_ids:
                continue
            if caller not in function_ids:
                continue
            if caller == callee:
                continue
            edges[caller].add(callee)

    return {k: frozenset(v) for k, v in edges.items()}


def functions_reachable_from(
    graph: dict[str, frozenset[str]], start: str
) -> frozenset[str]:
    """Functions reachable from ``start`` via call edges (start included)."""
    visited: set[str] = set()
    stack = [start]
    while stack:
        node = stack.pop()
        if node in visited:
            continue
        visited.add(node)
        stack.extend(graph.get(node, frozenset()))
    return frozenset(visited)
