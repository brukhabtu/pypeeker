"""Extract refactorings: extract-variable and extract-method."""

from __future__ import annotations

import textwrap
import uuid
from datetime import datetime, timezone

from pypeeker.models.scopes import ScopeKind
from pypeeker.models.transaction import (
    EditEntry,
    EditOp,
    TransactionHeader,
    TransactionSummary,
)
from pypeeker.refactor import cst
from pypeeker.refactor.dataflow import analyze_range, enclosing_function_scope
from pypeeker.storage import IndexStore, TransactionStore

# Selecting one of these means the user didn't select an expression.
_NON_EXPRESSION_TYPES = frozenset(
    {"module", "block", "function_definition", "class_definition"}
)


def _local_name(symbol_id: str) -> str:
    """Bare name of a local symbol id (``m:f:x`` -> ``x``)."""
    return symbol_id.rsplit(":", 1)[-1]



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


class ExtractMethodError(Exception):
    """Raised when an extract-method plan cannot be created."""


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
        if not new_name.isidentifier():
            raise ExtractMethodError(f"Invalid Python identifier: {new_name}")

        rdf = analyze_range(self._index_store, file_path, start_line, end_line)
        if rdf is None:
            raise ExtractMethodError("Range is not inside a function")
        if rdf.has_escape:
            raise ExtractMethodError(
                "Range contains return/break/continue; cannot extract safely"
            )

        index = self._index_store.load(file_path)
        func_scope = enclosing_function_scope(index.scopes, start_line, end_line)
        module_scope_id = next(
            (s.scope_id for s in index.scopes if s.kind == ScopeKind.MODULE), None
        )
        if func_scope.parent_scope_id != module_scope_id:
            raise ExtractMethodError(
                "extract-method v1 supports only top-level functions"
            )

        source_file = self._index_store.project_root / file_path
        source = source_file.read_text()
        file_hash = IndexStore.compute_file_hash(source_file)
        lines = source.splitlines(keepends=True)

        params = [_local_name(s) for s in rdf.inputs]
        returns = [_local_name(s) for s in rdf.outputs]

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

    @staticmethod
    def _line_starts(lines: list[str]) -> list[int]:
        """Byte offset of the start of each line."""
        offsets = []
        total = 0
        for line in lines:
            offsets.append(total)
            total += len(line.encode("utf-8"))
        return offsets
