"""Extract refactorings: extract-variable and extract-method."""

from __future__ import annotations

import textwrap
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Iterator

from pypeeker.models.symbol_id import leaf_name
from pypeeker.models.transaction import (
    EditEntry,
    EditOp,
    TransactionHeader,
    TransactionSummary,
)
from pypeeker.refactor import cst
from pypeeker.refactor.preconditions import (
    ExpressionFound,
    FileExists,
    FileFresh,
    InsideStatement,
    NoControlFlowEscape,
    Precondition,
    RangeInsideFunction,
    TopLevelFunctionOnly,
    ValidIdentifier,
    evaluate_in_order,
)
from pypeeker.storage import IndexStore, TransactionStore

if TYPE_CHECKING:
    from tree_sitter import Node

    from pypeeker.models.scopes import Scope
    from pypeeker.refactor.dataflow import RangeDataFlow


class ExtractVariableError(Exception):
    """Raised when an extract-variable plan cannot be created."""


@dataclass
class _ExtractVariableState:
    """Values computed while evaluating preconditions, reused to build edits."""

    source: bytes = b""
    file_hash: str = ""
    node: "Node | None" = None
    statement: "Node | None" = None


class ExtractVariablePlanner:
    """Plan an extract-variable refactor as a transaction."""

    def __init__(
        self, index_store: IndexStore, transaction_store: TransactionStore
    ) -> None:
        self._index_store = index_store
        self._transaction_store = transaction_store

    def plan(
        self,
        file_path: str,
        start: tuple[int, int],
        end: tuple[int, int],
        new_name: str,
    ) -> TransactionSummary:
        """Extract the expression spanning ``start``..``end`` into ``new_name``.

        ``start``/``end`` are 0-indexed ``(line, column)`` positions.
        """
        state = _ExtractVariableState()
        _, failure = evaluate_in_order(
            self._iter_preconditions(state, file_path, start, end, new_name)
        )
        if failure is not None:
            raise ExtractVariableError(failure.reason)

        source = state.source
        file_hash = state.file_hash
        node = state.node
        statement = state.statement

        expr_text = cst.node_text(node, source)
        indent = cst.indent_of(statement, source)
        insert_text = f"{indent}{new_name} = {expr_text}\n"

        edits = [
            cst.insert_edit(
                file_path, cst.line_start_byte(statement), insert_text, file_hash
            ),
            cst.replace_edit(file_path, node, new_name, file_hash, source),
        ]

        tx_id = uuid.uuid4().hex[:12]
        header = TransactionHeader(
            tx_id=tx_id,
            symbol_id="",
            old_name=expr_text,
            new_name=new_name,
            created_at=datetime.now(timezone.utc).isoformat(),
            operation="extract_variable",
        )
        self._transaction_store.save(header, edits, None)

        return TransactionSummary(
            tx_id=tx_id,
            operation="extract_variable",
            symbol_id="",
            old_name=expr_text,
            new_name=new_name,
            files_affected=[file_path],
            edit_count=len(edits),
            created_at=header.created_at,
        )

    def preconditions(
        self,
        file_path: str,
        start: tuple[int, int],
        end: tuple[int, int],
        new_name: str,
    ) -> list[Precondition]:
        """The ordered precondition set for this extraction, in enumerable form.

        Each precondition is evaluated as it is constructed (later ones are
        built from cached results of earlier ones, e.g. the inside-statement
        check needs the found expression node), so the returned objects
        reflect current state; if a precondition fails, the list ends at that
        precondition.
        """
        preconditions, _ = evaluate_in_order(
            self._iter_preconditions(
                _ExtractVariableState(), file_path, start, end, new_name
            )
        )
        return preconditions

    def _iter_preconditions(
        self,
        state: _ExtractVariableState,
        file_path: str,
        start: tuple[int, int],
        end: tuple[int, int],
        new_name: str,
    ) -> Iterator[Precondition]:
        """Yield this extraction's preconditions in evaluation order.

        The consumer must evaluate each yielded precondition before advancing
        (see :func:`evaluate_in_order`); the source bytes/hash and the parsed
        nodes are stashed on ``state`` for :meth:`plan`.
        """
        yield ValidIdentifier(new_name)
        yield FileExists(self._index_store, file_path)
        yield FileFresh(self._index_store, file_path)

        source_file = self._index_store.project_root / file_path
        state.source = source_file.read_bytes()
        state.file_hash = IndexStore.compute_file_hash(source_file)
        root = cst.parse(state.source)

        expression = ExpressionFound(root, start, end, file_path)
        yield expression
        state.node = expression.node

        statement = InsideStatement(expression.node)
        yield statement
        state.statement = statement.statement


class ExtractMethodError(Exception):
    """Raised when an extract-method plan cannot be created."""


@dataclass
class _ExtractMethodState:
    """Values computed while evaluating preconditions, reused to build edits."""

    dataflow: "RangeDataFlow | None" = None
    func_scope: "Scope | None" = None


class ExtractMethodPlanner:
    """Plan extracting a statement range into a new top-level function."""

    def __init__(
        self, index_store: IndexStore, transaction_store: TransactionStore
    ) -> None:
        self._index_store = index_store
        self._transaction_store = transaction_store

    def plan(
        self, file_path: str, start_line: int, end_line: int, new_name: str
    ) -> TransactionSummary:
        """Extract lines ``[start_line, end_line]`` (0-indexed) into ``new_name``.

        v1 supports extracting from a top-level function and refuses ranges that
        contain control-flow escapes (return/break/continue).
        """
        state = _ExtractMethodState()
        _, failure = evaluate_in_order(
            self._iter_preconditions(state, file_path, start_line, end_line, new_name)
        )
        if failure is not None:
            raise ExtractMethodError(failure.reason)

        rdf = state.dataflow
        func_scope = state.func_scope

        source_file = self._index_store.project_root / file_path
        source = source_file.read_text()
        file_hash = IndexStore.compute_file_hash(source_file)
        lines = source.splitlines(keepends=True)

        params = [leaf_name(s) for s in rdf.inputs]
        returns = [leaf_name(s) for s in rdf.outputs]

        range_text = "".join(lines[start_line : end_line + 1])
        body = textwrap.indent(textwrap.dedent(range_text), "    ")
        if not body.endswith("\n"):
            body += "\n"
        if returns:
            body += f"    return {', '.join(returns)}\n"
        new_func = f"def {new_name}({', '.join(params)}):\n{body}\n\n"

        call_indent = lines[start_line][: len(lines[start_line]) - len(lines[start_line].lstrip())]
        call_expr = f"{new_name}({', '.join(params)})"
        assignment = f"{', '.join(returns)} = " if returns else ""
        call_text = f"{call_indent}{assignment}{call_expr}\n"

        line_starts = self._line_starts(lines)
        range_start = line_starts[start_line]
        range_end = line_starts[end_line] + len(lines[end_line])
        func_start = line_starts[func_scope.span.start.line]

        edits = [
            EditEntry(
                file=file_path, start=func_start, end=func_start, old="",
                new=new_func, file_hash=file_hash, op=EditOp.INSERT,
            ),
            EditEntry(
                file=file_path, start=range_start, end=range_end,
                old=range_text, new=call_text, file_hash=file_hash,
                op=EditOp.REPLACE,
            ),
        ]

        tx_id = uuid.uuid4().hex[:12]
        header = TransactionHeader(
            tx_id=tx_id, symbol_id=func_scope.scope_id, old_name="",
            new_name=new_name,
            created_at=datetime.now(timezone.utc).isoformat(),
            operation="extract_method",
        )
        self._transaction_store.save(header, edits, None)
        return TransactionSummary(
            tx_id=tx_id, operation="extract_method",
            symbol_id=func_scope.scope_id, old_name="", new_name=new_name,
            files_affected=[file_path], edit_count=len(edits),
            created_at=header.created_at,
        )

    def preconditions(
        self, file_path: str, start_line: int, end_line: int, new_name: str
    ) -> list[Precondition]:
        """The ordered precondition set for this extraction, in enumerable form.

        Each precondition is evaluated as it is constructed (later ones are
        built from cached results of earlier ones, e.g. the escape check needs
        the range dataflow), so the returned objects reflect current state; if
        a precondition fails, the list ends at that precondition.
        """
        preconditions, _ = evaluate_in_order(
            self._iter_preconditions(
                _ExtractMethodState(), file_path, start_line, end_line, new_name
            )
        )
        return preconditions

    def _iter_preconditions(
        self,
        state: _ExtractMethodState,
        file_path: str,
        start_line: int,
        end_line: int,
        new_name: str,
    ) -> Iterator[Precondition]:
        """Yield this extraction's preconditions in evaluation order.

        The consumer must evaluate each yielded precondition before advancing
        (see :func:`evaluate_in_order`); the range dataflow and enclosing
        function scope are stashed on ``state`` for :meth:`plan`.
        """
        yield ValidIdentifier(new_name)
        yield FileFresh(self._index_store, file_path)

        in_function = RangeInsideFunction(
            self._index_store, file_path, start_line, end_line
        )
        yield in_function
        state.dataflow = in_function.dataflow

        yield NoControlFlowEscape(in_function.dataflow)

        top_level = TopLevelFunctionOnly(
            self._index_store, file_path, start_line, end_line
        )
        yield top_level
        state.func_scope = top_level.func_scope

    @staticmethod
    def _line_starts(lines: list[str]) -> list[int]:
        """Byte offset of the start of each line."""
        offsets = []
        total = 0
        for line in lines:
            offsets.append(total)
            total += len(line.encode("utf-8"))
        return offsets
