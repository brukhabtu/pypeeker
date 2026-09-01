"""Extract-variable and extract-method preconditions (:mod:`pypeeker.refactor.extract`)."""

from __future__ import annotations

from tree_sitter import Node

from pypeeker.models import (
    Scope,
    ScopeKind,
)
from pypeeker.refactor import cst
from pypeeker.refactor.dataflow import (
    RangeDataFlow,
    analyze_range,
    enclosing_function_scope,
)
from pypeeker.refactor.preconditions.base import (
    _PASS,
    Precondition,
    PreconditionResult,
    _fail,
)
from pypeeker.storage import IndexStore


# Selecting one of these means the user didn't select an expression.
_NON_EXPRESSION_TYPES = frozenset(
    {"module", "block", "function_definition", "class_definition"}
)


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
