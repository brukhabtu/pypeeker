"""Inline-variable preconditions (:mod:`pypeeker.refactor.inline`)."""

from __future__ import annotations

from tree_sitter import Node

from pypeeker.analysis import (
    VARIABLE_MUTATION,
    get_trait_provider,
)
from pypeeker.models import (
    FileIndex,
    ScopeKind,
    Symbol,
    SymbolKind,
)
from pypeeker.query import SemanticQueryEngine
from pypeeker.refactor import cst
from pypeeker.refactor.dataflow import (
    analyze_range,
)
from pypeeker.refactor.preconditions.base import (
    _PASS,
    Precondition,
    PreconditionResult,
    _fail,
)
from pypeeker.storage import IndexStore


# ---------------------------------------------------------------------------
# Inline-variable
# ---------------------------------------------------------------------------


class LocalVariableResolves(Precondition):
    """The symbol id resolves uniquely to a function-local variable.

    Caches the resolved symbol as :attr:`symbol` on a successful evaluation.
    """

    name = "local-variable-resolves"

    def __init__(
        self, engine: SemanticQueryEngine, index_store: IndexStore, symbol_id: str
    ) -> None:
        self._engine = engine
        self._index_store = index_store
        self.symbol_id = symbol_id
        self.symbol: Symbol | None = None

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        results = self._engine.find_symbol(self.symbol_id)
        if not results:
            return _fail(f"Symbol not found: {self.symbol_id}")
        if len(results) > 1:
            return _fail(f"Ambiguous symbol '{self.symbol_id}'; use the full id")
        symbol = results[0]
        if symbol.kind != SymbolKind.VARIABLE:
            return _fail("inline-variable only applies to variables")
        index = self._index_store.load(symbol.location.file_path)
        scope_kind = {
            s.scope_id: s.kind for s in (index.scopes if index else [])
        }.get(symbol.parent_scope_id)
        if scope_kind != ScopeKind.FUNCTION:
            return _fail("inline-variable v1 supports only function-local variables")
        self.symbol = symbol
        return _PASS


class LoadedIndexFresh(Precondition):
    """The variable's file has a loadable, non-stale index (inline).

    Caches the loaded index as :attr:`index` on a successful evaluation.
    """

    name = "loaded-index-fresh"

    def __init__(self, index_store: IndexStore, file_path: str) -> None:
        self._index_store = index_store
        self.file_path = file_path
        self.index: FileIndex | None = None

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        index = self._index_store.load(self.file_path)
        if index is None or self._index_store.is_stale(self.file_path):
            return _fail(f"File is stale or not indexed: {self.file_path}")
        self.index = index
        return _PASS


class NotReassigned(Precondition):
    """The variable has exactly one binding: no reassignment, shadowing or write.

    Takes the resolved symbol and its file index as constructor arguments
    (mid-plan values produced by :class:`LocalVariableResolves` and
    :class:`LoadedIndexFresh`).

    The write check is a pointwise verification of the same
    ``variable-mutation`` trait the ``prefer-tuple`` rule in :data:`pypeeker.dsl.RULES`
    quantifies over every candidate (see
    :mod:`pypeeker.analysis.variable_mutation`) — but only its
    ``has_write_ref`` fact, not the full ``is_mutated`` union: a
    ``.append()``/``.sort()``/... mutator call does not "reassign" a binding
    the way inlining means it (see that trait's docstring for the judgment
    call), so it must not fail this check the way it fails ``prefer_tuple``.
    """

    name = "not-reassigned"

    def __init__(self, symbol: Symbol, index: FileIndex) -> None:
        self.symbol = symbol
        self._index = index

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        # Shadowed/reassigned: a sibling symbol with the same name, a $N suffix,
        # or a WRITE reference (augmented/subscript/rebind) means more than one
        # binding — inlining is ambiguous.
        if "$" in self.symbol.symbol_id:
            return _fail("Variable is reassigned; cannot inline")
        for s in self._index.symbols:
            if (
                s.symbol_id != self.symbol.symbol_id
                and s.parent_scope_id == self.symbol.parent_scope_id
                and s.name == self.symbol.name
            ):
                return _fail("Variable is reassigned; cannot inline")
        mutation_trait = get_trait_provider(VARIABLE_MUTATION)
        assert mutation_trait is not None, (
            f"'{VARIABLE_MUTATION}' trait provider not registered — "
            "pypeeker.analysis.variable_mutation failed to import"
        )
        if mutation_trait(self._index, self.symbol.symbol_id).value.has_write_ref:
            return _fail("Variable is reassigned; cannot inline")
        return _PASS


class MultiUseValuePure(Precondition):
    """A value duplicated across multiple uses must be side-effect-free.

    Takes the number of READ references as a constructor argument (mid-plan
    value: the planner collects the reads it will rewrite).
    """

    name = "multi-use-value-pure"

    def __init__(
        self, index_store: IndexStore, file_path: str, def_line: int, use_count: int
    ) -> None:
        self._index_store = index_store
        self.file_path = file_path
        self.def_line = def_line
        self.use_count = use_count

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.use_count > 1:
            dataflow = analyze_range(
                self._index_store, self.file_path, self.def_line, self.def_line
            )
            if dataflow is None or not dataflow.is_pure:
                return _fail(
                    "Value has side effects and is used more than once; "
                    "inlining would change behavior"
                )
        return _PASS


class AssignmentLocatable(Precondition):
    """The variable's binding is a simple assignment with an inlinable value.

    Takes the parsed CST root and the resolved symbol as constructor
    arguments (mid-plan values) and caches the assignment's right-hand side
    as :attr:`rhs` on a successful evaluation.
    """

    name = "assignment-locatable"

    def __init__(self, root: Node, symbol: Symbol) -> None:
        self._root = root
        self.symbol = symbol
        self.rhs: Node | None = None

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        target = cst.expression_at(
            self._root,
            self.symbol.location.span.start.line,
            self.symbol.location.span.start.column,
        )
        if target is None:
            return _fail("Could not locate the assignment")
        node = target
        while node is not None and node.type != "assignment":
            node = node.parent
        if node is None:
            return _fail("Variable is not a simple assignment")
        rhs = node.child_by_field_name("right")
        if rhs is None:
            return _fail("Assignment has no value to inline")
        self.rhs = rhs
        return _PASS
