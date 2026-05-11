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
``other_module.func()``, ``from lib import f; f()``) are caught precisely.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from pypeeker.models.references import ReferenceKind
from pypeeker.models.symbols import SymbolKind
from pypeeker.storage import IndexStore


@dataclass(frozen=True)
class TransitiveImpureCall:
    """A direct call to another function that's been classified as impure."""

    callee: str
    """``symbol_id`` of the impure callee."""
    line: int | None = None


def call_graph(store: IndexStore) -> dict[str, frozenset[str]]:
    """Return ``{caller_function_id -> frozenset[callee_function_id]}``.

    Both caller and callee must be FUNCTION or METHOD symbols. Module-level
    code, class bodies, and class symbols are not represented as callers
    or callees in v1.

    Cross-file imports (``from lib import helper; helper()``) resolve to a
    local IMPORT symbol — we follow ``imported_from`` to translate the
    callee back to the original function symbol_id.
    """
    function_ids: set[str] = set()
    import_targets: dict[str, str] = {}

    for source_path in store.list_indexed_files():
        index = store.load(source_path)
        if index is None:
            continue
        for s in index.symbols:
            if s.kind in (SymbolKind.FUNCTION, SymbolKind.METHOD):
                function_ids.add(s.symbol_id)

    for source_path in store.list_indexed_files():
        index = store.load(source_path)
        if index is None:
            continue
        for s in index.symbols:
            if s.kind != SymbolKind.IMPORT or not s.imported_from:
                continue
            resolved = _resolve_import_target(s.imported_from, function_ids)
            if resolved is not None:
                import_targets[s.symbol_id] = resolved

    edges: dict[str, set[str]] = defaultdict(set)
    for source_path in store.list_indexed_files():
        index = store.load(source_path)
        if index is None:
            continue
        for ref in index.references:
            if ref.kind != ReferenceKind.CALL:
                continue
            if not ref.resolved:
                continue
            callee = import_targets.get(ref.symbol_id, ref.symbol_id)
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


def _resolve_import_target(
    imported_from: str, function_ids: set[str]
) -> str | None:
    """Translate ``module.name`` style ``imported_from`` into a function_id.

    Tries ``module.py:name`` first, then ``module/__init__.py:name`` for
    package-relative imports. Returns None if neither matches a known
    function symbol.
    """
    parts = imported_from.split(".")
    if len(parts) < 2:
        return None
    module_path = "/".join(parts[:-1])
    name = parts[-1]
    for candidate in (
        f"{module_path}.py:{name}",
        f"{module_path}/__init__.py:{name}",
    ):
        if candidate in function_ids:
            return candidate
    return None
