"""Extract-variable refactoring.

Given a selected expression, introduce a new local bound to it on the line
above and replace the selection with the new name. The smallest end-to-end
complex refactor: it analyses nothing cross-file, but exercises the full
plan-on-CST → transaction → apply pipeline with INSERT + REPLACE edits.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pypeeker.models.transaction import TransactionHeader, TransactionSummary
from pypeeker.refactor import cst
from pypeeker.storage import IndexStore, TransactionStore

# Selecting one of these means the user didn't select an expression.
_NON_EXPRESSION_TYPES = frozenset(
    {"module", "block", "function_definition", "class_definition"}
)


class ExtractVariableError(Exception):
    """Raised when an extract-variable plan cannot be created."""


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
        if not new_name.isidentifier():
            raise ExtractVariableError(f"Invalid Python identifier: {new_name}")

        source_file = self._index_store.project_root / file_path
        if not source_file.exists():
            raise ExtractVariableError(f"File not found: {file_path}")
        source = source_file.read_bytes()
        file_hash = IndexStore.compute_file_hash(source_file)

        root = cst.parse(source)
        node = cst.node_spanning(root, start, end)
        if node is None or node.type in _NON_EXPRESSION_TYPES or node.parent is None:
            raise ExtractVariableError(
                f"No expression found at {file_path}:{start[0]}:{start[1]}"
            )

        statement = cst.enclosing_statement(node)
        if statement is None:
            raise ExtractVariableError("Selection is not inside a statement")

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
