"""First-class planner preconditions.

Each refactor planner (rename, extract-variable, extract-method,
inline-variable) historically validated its prerequisites with inline
``raise`` statements. This module lifts those checks into small, named
:class:`Precondition` objects so they can be evaluated independently of the
planners — the composite batch planner re-validates "guarded" intents at
materialization time by re-running a planner's precondition set (TASK-88).

Contract
--------
- A precondition has a stable :attr:`Precondition.name` and an
  ``evaluate() -> PreconditionResult`` method returning a pass/fail flag
  plus, on failure, a reason string identical to the planner's historical
  error message. ``evaluate()`` never raises for an ordinary failed check.
- Inputs are passed explicitly at construction — usually the
  :class:`~pypeeker.storage.IndexStore` /
  :class:`~pypeeker.query.engine.SemanticQueryEngine` and the plan
  parameters. Preconditions that need values computed mid-plan (the resolved
  symbol for rename's conflict check, the parsed CST for extract-variable,
  the range dataflow for extract-method) take those values as constructor
  arguments. Each planner's ``preconditions(...)`` method recomputes those
  inputs, so a caller that wants a check against *current* state should
  rebuild the set through that method rather than re-evaluating cached
  instances whose constructor inputs may have gone stale.
- Resolution-style preconditions cache what they resolved (e.g.
  :attr:`SymbolResolvesUniquely.symbol`) on a successful ``evaluate()`` so
  the planner and dependent preconditions can reuse it without re-querying.
- :func:`evaluate_in_order` drives an ordered set, stopping at the first
  failure. It accepts generators that construct later preconditions from the
  cached results of earlier ones; the generator is never advanced past a
  failing precondition.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Iterable

from pypeeker.models.references import ReferenceKind
from pypeeker.models.scopes import ScopeKind
from pypeeker.models.symbols import SymbolKind
from pypeeker.refactor import cst
from pypeeker.refactor.dataflow import analyze_range, enclosing_function_scope

if TYPE_CHECKING:
    from tree_sitter import Node

    from pypeeker.models.index import FileIndex
    from pypeeker.models.scopes import Scope
    from pypeeker.models.symbols import Symbol
    from pypeeker.query.engine import SemanticQueryEngine
    from pypeeker.refactor.dataflow import RangeDataFlow
    from pypeeker.storage import IndexStore

# Selecting one of these means the user didn't select an expression.
_NON_EXPRESSION_TYPES = frozenset(
    {"module", "block", "function_definition", "class_definition"}
)


@dataclass(frozen=True)
class PreconditionResult:
    """Outcome of evaluating a single precondition."""

    ok: bool
    reason: str = ""


_PASS = PreconditionResult(ok=True)


def _fail(reason: str) -> PreconditionResult:
    return PreconditionResult(ok=False, reason=reason)


class Precondition(ABC):
    """A named, independently evaluable prerequisite of a refactor plan."""

    name: ClassVar[str]

    @abstractmethod
    def evaluate(self) -> PreconditionResult:
        """Check the precondition; report failure via the result, not by raising."""


def evaluate_in_order(
    preconditions: Iterable[Precondition],
) -> tuple[list[Precondition], PreconditionResult | None]:
    """Evaluate preconditions in order, stopping at the first failure.

    Returns ``(evaluated, failure)``: ``evaluated`` holds every precondition
    that was constructed (the failing one last, if any) and ``failure`` is
    the failing :class:`PreconditionResult`, or ``None`` if all passed.

    The iterable may be a generator that constructs later preconditions from
    the cached results of earlier ones (e.g. the resolved symbol); it is not
    advanced past a failing precondition, so dependent construction only runs
    once its prerequisites hold.
    """
    evaluated: list[Precondition] = []
    for precondition in preconditions:
        evaluated.append(precondition)
        result = precondition.evaluate()
        if not result.ok:
            return evaluated, result
    return evaluated, None


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------


class ValidIdentifier(Precondition):
    """The new name is a valid Python identifier (rename and extract)."""

    name = "valid-identifier"

    def __init__(self, new_name: str) -> None:
        self.new_name = new_name

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if not self.new_name.isidentifier():
            return _fail(f"Invalid Python identifier: {self.new_name}")
        return _PASS


class FileExists(Precondition):
    """The target file exists on disk (extract-variable)."""

    name = "file-exists"

    def __init__(self, index_store: IndexStore, file_path: str) -> None:
        self._index_store = index_store
        self.file_path = file_path

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if not (self._index_store.project_root / self.file_path).exists():
            return _fail(f"File not found: {self.file_path}")
        return _PASS


class FileFresh(Precondition):
    """The target file is indexed and the index is not stale (extract)."""

    name = "file-fresh"

    def __init__(self, index_store: IndexStore, file_path: str) -> None:
        self._index_store = index_store
        self.file_path = file_path

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self._index_store.is_stale(self.file_path):
            return _fail(f"File is stale or not indexed: {self.file_path}")
        return _PASS


# ---------------------------------------------------------------------------
# Rename
# ---------------------------------------------------------------------------


class RenameFlagsCompatible(Precondition):
    """``--include-exports`` and ``--keep-export`` are not combined."""

    name = "rename-flags-compatible"

    def __init__(self, include_exports: bool, keep_export: bool) -> None:
        self.include_exports = include_exports
        self.keep_export = keep_export

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.include_exports and self.keep_export:
            return _fail(
                "--include-exports and --keep-export are mutually exclusive: "
                "one changes the public export name, the other preserves it."
            )
        return _PASS


class SymbolResolvesUniquely(Precondition):
    """The symbol id matches exactly one symbol (rename).

    Caches the resolved symbol as :attr:`symbol` on a successful evaluation.
    """

    name = "symbol-resolves-uniquely"

    def __init__(self, engine: SemanticQueryEngine, symbol_id: str) -> None:
        self._engine = engine
        self.symbol_id = symbol_id
        self.symbol: Symbol | None = None

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        results = self._engine.find_symbol(self.symbol_id)
        if not results:
            return _fail(f"Symbol not found: {self.symbol_id}")
        if len(results) > 1:
            ids = [s.symbol_id for s in results]
            return _fail(
                f"Ambiguous symbol '{self.symbol_id}', matched {len(results)}: {ids}. "
                "Use the full symbol ID to disambiguate."
            )
        self.symbol = results[0]
        return _PASS


class NewNameDiffers(Precondition):
    """The new name differs from the symbol's current name."""

    name = "new-name-differs"

    def __init__(self, old_name: str, new_name: str) -> None:
        self.old_name = old_name
        self.new_name = new_name

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.old_name == self.new_name:
            return _fail(f"New name is same as old name: {self.new_name}")
        return _PASS


class NoScopeNameConflict(Precondition):
    """No sibling symbol in the same scope already uses the new name.

    Takes the resolved symbol as a constructor argument (mid-plan value
    produced by :class:`SymbolResolvesUniquely`).
    """

    name = "no-scope-name-conflict"

    def __init__(self, index_store: IndexStore, symbol: Symbol, new_name: str) -> None:
        self._index_store = index_store
        self.symbol = symbol
        self.new_name = new_name

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.symbol.parent_scope_id:
            index = self._index_store.load(self.symbol.location.file_path)
            if index:
                for s in index.symbols:
                    if (
                        s.parent_scope_id == self.symbol.parent_scope_id
                        and s.name == self.new_name
                        and s.symbol_id != self.symbol.symbol_id
                    ):
                        return _fail(
                            f"Name conflict: '{self.new_name}' already exists in scope "
                            f"'{self.symbol.parent_scope_id}' as {s.symbol_id}"
                        )
        return _PASS


class AffectedFilesFresh(Precondition):
    """All files the rename touches are indexed and not stale.

    Takes the computed set of affected files as a constructor argument
    (mid-plan value: the planner derives it from the edit locations).
    """

    name = "affected-files-fresh"

    def __init__(self, index_store: IndexStore, file_paths: Iterable[str]) -> None:
        self._index_store = index_store
        self.file_paths = set(file_paths)

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        for fp in self.file_paths:
            if self._index_store.is_stale(fp):
                return _fail(
                    f"File '{fp}' is stale or not indexed. "
                    "Run 'pypeeker index' first."
                )
        return _PASS


# ---------------------------------------------------------------------------
# Extract-variable
# ---------------------------------------------------------------------------


class ExpressionFound(Precondition):
    """The selected range covers an expression node.

    Takes the parsed CST root as a constructor argument (mid-plan value) and
    caches the found node as :attr:`node` on a successful evaluation.
    """

    name = "expression-found"

    def __init__(
        self,
        root: Node,
        start: tuple[int, int],
        end: tuple[int, int],
        file_path: str,
    ) -> None:
        self._root = root
        self.start = start
        self.end = end
        self.file_path = file_path
        self.node: Node | None = None

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        node = cst.node_spanning(self._root, self.start, self.end)
        if node is None or node.type in _NON_EXPRESSION_TYPES or node.parent is None:
            return _fail(
                f"No expression found at {self.file_path}:{self.start[0]}:{self.start[1]}"
            )
        self.node = node
        return _PASS


class InsideStatement(Precondition):
    """The selected expression sits inside a statement.

    Takes the found expression node as a constructor argument (mid-plan value
    produced by :class:`ExpressionFound`) and caches the enclosing statement
    as :attr:`statement` on a successful evaluation.
    """

    name = "inside-statement"

    def __init__(self, node: Node) -> None:
        self._node = node
        self.statement: Node | None = None

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        statement = cst.enclosing_statement(self._node)
        if statement is None:
            return _fail("Selection is not inside a statement")
        self.statement = statement
        return _PASS


# ---------------------------------------------------------------------------
# Extract-method
# ---------------------------------------------------------------------------


class RangeInsideFunction(Precondition):
    """The line range lies inside a function.

    Caches the range dataflow summary as :attr:`dataflow` on a successful
    evaluation.
    """

    name = "range-inside-function"

    def __init__(
        self, index_store: IndexStore, file_path: str, start_line: int, end_line: int
    ) -> None:
        self._index_store = index_store
        self.file_path = file_path
        self.start_line = start_line
        self.end_line = end_line
        self.dataflow: RangeDataFlow | None = None

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        dataflow = analyze_range(
            self._index_store, self.file_path, self.start_line, self.end_line
        )
        if dataflow is None:
            return _fail("Range is not inside a function")
        self.dataflow = dataflow
        return _PASS


class NoControlFlowEscape(Precondition):
    """The range contains no return/break/continue.

    Takes the range dataflow as a constructor argument (mid-plan value
    produced by :class:`RangeInsideFunction`).
    """

    name = "no-control-flow-escape"

    def __init__(self, dataflow: RangeDataFlow) -> None:
        self.dataflow = dataflow

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.dataflow.has_escape:
            return _fail(
                "Range contains return/break/continue; cannot extract safely"
            )
        return _PASS


class TopLevelFunctionOnly(Precondition):
    """The enclosing function is a top-level (module-scope) function.

    Caches the enclosing function scope as :attr:`func_scope` on a successful
    evaluation.
    """

    name = "top-level-function-only"

    def __init__(
        self, index_store: IndexStore, file_path: str, start_line: int, end_line: int
    ) -> None:
        self._index_store = index_store
        self.file_path = file_path
        self.start_line = start_line
        self.end_line = end_line
        self.func_scope: Scope | None = None

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        index = self._index_store.load(self.file_path)
        func_scope = (
            enclosing_function_scope(index.scopes, self.start_line, self.end_line)
            if index is not None
            else None
        )
        if func_scope is None:
            # Out-of-contract standalone use (file not indexed / no function);
            # in the planner this is caught earlier by RangeInsideFunction.
            return _fail("Range is not inside a function")
        module_scope_id = next(
            (s.scope_id for s in index.scopes if s.kind == ScopeKind.MODULE), None
        )
        if func_scope.parent_scope_id != module_scope_id:
            return _fail("extract-method v1 supports only top-level functions")
        self.func_scope = func_scope
        return _PASS


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
        for ref in self._index.references:
            if (
                ref.symbol_id == self.symbol.symbol_id
                and ref.kind == ReferenceKind.WRITE
            ):
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
