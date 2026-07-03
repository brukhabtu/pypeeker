"""Inline-variable refactoring.

Replace each use of a local variable with its assigned value and delete the
assignment. Safety comes from the semantic layer (single assignment, all
references, purity of a value duplicated across multiple uses); the CST layer
performs the byte-precise rewrite.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Iterator

from pypeeker.models import (
    EditEntry,
    EditOp,
    Reference,
    ReferenceKind,
    Symbol,
    TransactionHeader,
    TransactionSummary,
)
from pypeeker.query import SemanticQueryEngine
from pypeeker.refactor import cst
from pypeeker.refactor.preconditions import (
    AssignmentLocatable,
    LoadedIndexFresh,
    LocalVariableResolves,
    MultiUseValuePure,
    NotReassigned,
    Precondition,
    evaluate_in_order,
)
from pypeeker.storage import IndexStore, TransactionStore

if TYPE_CHECKING:
    from tree_sitter import Node

# Expression node types that need parentheses when inlined into a larger
# expression (precedence-sensitive); atoms can be substituted bare.
_NEEDS_PARENS = frozenset(
    {
        "binary_operator",
        "boolean_operator",
        "comparison_operator",
        "not_operator",
        "unary_operator",
        "conditional_expression",
        "lambda",
        "await",
        "named_expression",
        "yield",
    }
)


class InlineVariableError(Exception):
    """Raised when an inline-variable plan cannot be created."""


@dataclass
class _InlineVariableState:
    """Values computed while evaluating preconditions, reused to build edits."""

    symbol: Symbol | None = None
    reads: list[Reference] = field(default_factory=list)
    source: bytes = b""
    file_hash: str = ""
    root: "Node | None" = None
    rhs: "Node | None" = None


class InlineVariablePlanner:
    """Plan inlining a local variable into its uses as a transaction."""

    def __init__(
        self, index_store: IndexStore, transaction_store: TransactionStore
    ) -> None:
        self._index_store = index_store
        self._transaction_store = transaction_store
        self._engine = SemanticQueryEngine(index_store)

    def plan(self, symbol_id: str) -> TransactionSummary:
        """Inline the local variable ``symbol_id`` into its uses."""
        state = _InlineVariableState()
        _, failure = evaluate_in_order(self._iter_preconditions(state, symbol_id))
        if failure is not None:
            raise InlineVariableError(failure.reason)

        symbol = state.symbol
        file_path = symbol.location.file_path
        reads = state.reads
        source = state.source
        file_hash = state.file_hash
        root = state.root
        rhs = state.rhs

        rhs_text = cst.node_text(rhs, source)
        replacement = f"({rhs_text})" if rhs.type in _NEEDS_PARENS else rhs_text

        edits = [self._delete_assignment_edit(root, symbol, source, file_hash)]
        for ref in reads:
            node = cst.expression_at(
                root, ref.location.span.start.line, ref.location.span.start.column
            )
            if node is not None and cst.node_text(node, source) == symbol.name:
                edits.append(cst.replace_edit(file_path, node, replacement, file_hash, source))

        tx_id = uuid.uuid4().hex[:12]
        header = TransactionHeader(
            tx_id=tx_id, symbol_id=symbol.symbol_id, old_name=symbol.name,
            new_name=rhs_text,
            created_at=datetime.now(timezone.utc).isoformat(),
            operation="inline_variable",
        )
        self._transaction_store.save(header, edits, None)
        return TransactionSummary(
            tx_id=tx_id, operation="inline_variable",
            symbol_id=symbol.symbol_id, old_name=symbol.name, new_name=rhs_text,
            files_affected=[file_path], edit_count=len(edits),
            created_at=header.created_at,
        )

    def preconditions(self, symbol_id: str) -> list[Precondition]:
        """The ordered precondition set for this inline, in enumerable form.

        Each precondition is evaluated as it is constructed (later ones are
        built from cached results of earlier ones, e.g. the reassignment check
        needs the resolved symbol and loaded index), so the returned objects
        reflect current state; if a precondition fails, the list ends at that
        precondition.
        """
        preconditions, _ = evaluate_in_order(
            self._iter_preconditions(_InlineVariableState(), symbol_id)
        )
        return preconditions

    def _iter_preconditions(
        self, state: _InlineVariableState, symbol_id: str
    ) -> Iterator[Precondition]:
        """Yield this inline's preconditions in evaluation order.

        The consumer must evaluate each yielded precondition before advancing
        (see :func:`evaluate_in_order`); the resolved symbol, READ references,
        source bytes/hash, parsed root and assignment RHS are stashed on
        ``state`` for :meth:`plan`.
        """
        resolve = LocalVariableResolves(self._engine, self._index_store, symbol_id)
        yield resolve
        symbol = resolve.symbol
        state.symbol = symbol
        file_path = symbol.location.file_path

        fresh = LoadedIndexFresh(self._index_store, file_path)
        yield fresh

        yield NotReassigned(symbol, fresh.index)

        state.reads = [
            r
            for r in self._engine.references_to_binding(symbol.symbol_id)
            if r.kind == ReferenceKind.READ
        ]
        def_line = symbol.location.span.start.line
        yield MultiUseValuePure(
            self._index_store, file_path, def_line, len(state.reads)
        )

        source_file = self._index_store.project_root / file_path
        state.source = source_file.read_bytes()
        state.file_hash = IndexStore.compute_file_hash(source_file)
        state.root = cst.parse(state.source)

        locatable = AssignmentLocatable(state.root, symbol)
        yield locatable
        state.rhs = locatable.rhs

    def _delete_assignment_edit(
        self, root, symbol: Symbol, source: bytes, file_hash: str
    ) -> EditEntry:
        target = cst.expression_at(
            root,
            symbol.location.span.start.line,
            symbol.location.span.start.column,
        )
        statement = cst.enclosing_statement(target)
        start = cst.line_start_byte(statement)
        line_starts = _line_start_bytes(source)
        end_line = statement.end_point[0]
        end = line_starts[end_line + 1] if end_line + 1 < len(line_starts) else len(source)
        return EditEntry(
            file=symbol.location.file_path, start=start, end=end,
            old=source[start:end].decode("utf-8"), new="",
            file_hash=file_hash, op=EditOp.DELETE,
        )


def _line_start_bytes(source: bytes) -> list[int]:
    """Byte offset of the start of each line (plus a sentinel past the end)."""
    offsets = [0]
    for i, byte in enumerate(source):
        if byte == 0x0A:  # newline
            offsets.append(i + 1)
    return offsets
