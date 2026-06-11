"""Inline-variable refactoring.

Replace each use of a local variable with its assigned value and delete the
assignment. Safety comes from the semantic layer (single assignment, all
references, purity of a value duplicated across multiple uses); the CST layer
performs the byte-precise rewrite.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pypeeker.models.references import ReferenceKind
from pypeeker.models.scopes import ScopeKind
from pypeeker.models.symbols import Symbol, SymbolKind
from pypeeker.models.transaction import (
    EditEntry,
    EditOp,
    TransactionHeader,
    TransactionSummary,
)
from pypeeker.query.engine import SemanticQueryEngine
from pypeeker.refactor import cst
from pypeeker.refactor.dataflow import analyze_range
from pypeeker.storage import IndexStore, TransactionStore

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
        symbol = self._resolve_local_variable(symbol_id)
        file_path = symbol.location.file_path

        index = self._index_store.load(file_path)
        if index is None or self._index_store.is_stale(file_path):
            raise InlineVariableError(f"File is stale or not indexed: {file_path}")

        self._reject_reassignment(symbol, index)

        reads = [
            r
            for r in self._engine.references_to_binding(symbol.symbol_id)
            if r.kind == ReferenceKind.READ
        ]
        def_line = symbol.location.span.start.line
        if len(reads) > 1:
            rdf = analyze_range(self._index_store, file_path, def_line, def_line)
            if rdf is None or not rdf.is_pure:
                raise InlineVariableError(
                    "Value has side effects and is used more than once; "
                    "inlining would change behavior"
                )

        source_file = self._index_store.project_root / file_path
        source = source_file.read_bytes()
        file_hash = IndexStore.compute_file_hash(source_file)
        root = cst.parse(source)

        rhs = self._assignment_rhs(root, symbol)
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

    def _resolve_local_variable(self, symbol_id: str) -> Symbol:
        results = self._engine.find_symbol(symbol_id)
        if not results:
            raise InlineVariableError(f"Symbol not found: {symbol_id}")
        if len(results) > 1:
            raise InlineVariableError(
                f"Ambiguous symbol '{symbol_id}'; use the full id"
            )
        symbol = results[0]
        if symbol.kind != SymbolKind.VARIABLE:
            raise InlineVariableError("inline-variable only applies to variables")
        index = self._index_store.load(symbol.location.file_path)
        scope_kind = {
            s.scope_id: s.kind for s in (index.scopes if index else [])
        }.get(symbol.parent_scope_id)
        if scope_kind != ScopeKind.FUNCTION:
            raise InlineVariableError(
                "inline-variable v1 supports only function-local variables"
            )
        return symbol

    def _reject_reassignment(self, symbol: Symbol, index) -> None:
        # Shadowed/reassigned: a sibling symbol with the same name, a $N suffix,
        # or a WRITE reference (augmented/subscript/rebind) means more than one
        # binding — inlining is ambiguous.
        if "$" in symbol.symbol_id:
            raise InlineVariableError("Variable is reassigned; cannot inline")
        for s in index.symbols:
            if (
                s.symbol_id != symbol.symbol_id
                and s.parent_scope_id == symbol.parent_scope_id
                and s.name == symbol.name
            ):
                raise InlineVariableError("Variable is reassigned; cannot inline")
        for ref in index.references:
            if ref.symbol_id == symbol.symbol_id and ref.kind == ReferenceKind.WRITE:
                raise InlineVariableError("Variable is reassigned; cannot inline")

    def _assignment_rhs(self, root, symbol: Symbol):
        target = cst.expression_at(
            root,
            symbol.location.span.start.line,
            symbol.location.span.start.column,
        )
        if target is None:
            raise InlineVariableError("Could not locate the assignment")
        node = target
        while node is not None and node.type != "assignment":
            node = node.parent
        if node is None:
            raise InlineVariableError("Variable is not a simple assignment")
        rhs = node.child_by_field_name("right")
        if rhs is None:
            raise InlineVariableError("Assignment has no value to inline")
        return rhs

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
