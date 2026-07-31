"""Builtin rule: no-import-cycles (module-level import-cycle detection).

``import-boundaries`` enforces an acyclic *package* DAG but is blind to cycles
between *modules* within a package. This rule builds the module-level import
graph and reports each strongly-connected group of >= 2 modules as a cycle.

Only imports that execute at *module-load time* count — those in the module
body or a class body. ``TYPE_CHECKING``-guarded imports are included (the binder
recovers them as ``IMPORT`` symbols, so a cycle hidden under
``if TYPE_CHECKING:`` is caught just like a runtime one), but a *function-local*
(deferred) import is excluded: it runs only when the function is called, so it
creates no module-load cycle — it is the idiomatic way to *break* one, as the
binder's own recursive-descent visitors do when they import the ``visit_node``
dispatcher inside their bodies.

Each import is charged to its *origin* module — the module that actually defines
the imported name, resolved through re-export chains by the shared
:class:`CrossModuleResolver` (the same origin resolution ``import-boundaries``
uses). Edges to external / unindexed targets (stdlib, third-party) are dropped,
since those are not project modules and never close a project cycle.

Fix a cycle by importing the type directly; if that would itself cycle, extract
the shared contract to a sibling module both sides can depend on (Dependency
Inversion), as ``check/context.py`` does for the project-rule contract.

Options (``[tool.pypeeker.no-import-cycles]``):
    ``allow`` — list of accepted cycles, each a list of the dotted module names
                in the cycle. A detected cycle whose member set matches one is
                suppressed — an explicit escape hatch for a cycle you have
                deliberately chosen to keep.

Import discipline: imports only concrete ``pypeeker.check.*`` modules —
importing ``pypeeker.check`` itself from a builtin rule module recurses into
the engine import and creates a cycle.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from pypeeker.check.context import CheckContext
from pypeeker.check.models import Violation
from pypeeker.check.rules import register_rule
from pypeeker.models import Scope, ScopeKind, SymbolKind, module_of

_DEFERRED_SCOPE_KINDS = frozenset(
    {ScopeKind.FUNCTION, ScopeKind.LAMBDA, ScopeKind.COMPREHENSION}
)

NO_IMPORT_CYCLES = "no-import-cycles"


@register_rule(NO_IMPORT_CYCLES, scope="project")
def _no_import_cycles(
    context: CheckContext, options: Mapping[str, Any]
) -> list[Violation]:
    """Report module-level import cycles. See the module docstring."""
    resolver = context.resolver

    module_of_file: dict[str, str] = {}
    for index in context.indexes:
        module_id = next(
            (s.symbol_id for s in index.symbols if s.kind == SymbolKind.MODULE),
            None,
        )
        if module_id is not None:
            module_of_file[index.file_path] = module_id
    project_modules = set(module_of_file.values())
    module_to_file = {module: path for path, module in module_of_file.items()}

    # Directed module graph. Keep one representative import location per
    # (importer, origin) edge so a finding can point at a real import line.
    edges: dict[str, set[str]] = {module: set() for module in project_modules}
    edge_site: dict[tuple[str, str], tuple[str, int]] = {}
    for index in context.indexes:
        importer = module_of_file.get(index.file_path)
        if importer is None:
            continue
        scope_by_id = {scope.scope_id: scope for scope in index.scopes}
        for symbol in index.symbols:
            if symbol.kind is not SymbolKind.IMPORT:
                continue
            if not _runs_at_import_time(symbol.parent_scope_id, scope_by_id):
                continue
            origin = module_of(resolver.resolve_definition(symbol.symbol_id))
            if origin == importer or origin not in project_modules:
                continue
            edges[importer].add(origin)
            edge_site.setdefault(
                (importer, origin),
                (symbol.location.file_path, symbol.location.span.start.line + 1),
            )

    allowed = _allowed_cycles(options)

    violations: list[Violation] = []
    for component in _strongly_connected_components(edges):
        if len(component) < 2:
            continue
        members = sorted(component)
        if frozenset(members) in allowed:
            continue
        component_set = set(component)
        file_path, line = next(
            (
                edge_site[(a, b)]
                for a in members
                for b in sorted(edges[a])
                if b in component_set and (a, b) in edge_site
            ),
            (module_to_file.get(members[0], members[0]), 1),
        )
        violations.append(
            Violation(
                file_path=file_path,
                line=line,
                rule=NO_IMPORT_CYCLES,
                message=(
                    "import cycle among modules: "
                    + ", ".join(members)
                    + " — import the type directly, or extract the shared "
                    "contract to a sibling module both can depend on"
                ),
            )
        )
    return violations


def _runs_at_import_time(
    parent_scope_id: str | None, scope_by_id: Mapping[str, Scope]
) -> bool:
    """Whether an import in ``parent_scope_id`` executes at module load.

    True when every enclosing scope up to the module is a module or class body
    (both run at import time). A ``function``/``lambda``/``comprehension`` scope
    anywhere on the path means the import is deferred to call time, so it forms
    no module-load cycle. A missing/unknown scope is treated as load-time — the
    conservative choice that still reports the edge.
    """
    scope_id = parent_scope_id
    while scope_id is not None:
        scope = scope_by_id.get(scope_id)
        if scope is None:
            return True
        if scope.kind in _DEFERRED_SCOPE_KINDS:
            return False
        scope_id = scope.parent_scope_id
    return True


def _allowed_cycles(options: Mapping[str, Any]) -> set[frozenset[str]]:
    """Parse ``allow`` into a set of accepted-cycle member frozensets."""
    raw = options.get("allow")
    if not isinstance(raw, list):
        return set()
    accepted: set[frozenset[str]] = set()
    for entry in raw:
        if isinstance(entry, list):
            accepted.add(frozenset(str(module) for module in entry))
    return accepted


def _strongly_connected_components(
    graph: Mapping[str, Iterable[str]],
) -> list[list[str]]:
    """Tarjan's SCC algorithm, iterative (safe on deep graphs).

    Returns one list of node names per strongly-connected component. A
    component of size >= 2 (or a self-loop) is a cycle.
    """
    counter = 0
    order: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    stack: list[str] = []
    result: list[list[str]] = []

    for root in graph:
        if root in order:
            continue
        work: list[tuple[str, list[str]]] = [(root, sorted(graph[root]))]
        order[root] = lowlink[root] = counter
        counter += 1
        stack.append(root)
        on_stack[root] = True
        while work:
            node, neighbours = work[-1]
            descended = False
            while neighbours:
                nxt = neighbours[0]
                if nxt not in order:
                    order[nxt] = lowlink[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack[nxt] = True
                    work.append((nxt, sorted(graph.get(nxt, ()))))
                    descended = True
                    break
                if on_stack.get(nxt):
                    lowlink[node] = min(lowlink[node], order[nxt])
                neighbours.pop(0)
            if descended:
                continue
            if lowlink[node] == order[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack[member] = False
                    component.append(member)
                    if member == node:
                        break
                result.append(component)
            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])
    return result
