"""delete-symbol planner: remove an unreferenced FUNCTION/CLASS definition.

Ports the superseded ``unused-public-symbol`` autofix verbatim in behavior
as :class:`~pypeeker.intents.DeleteSymbolIntent`'s (``"delete-symbol"``)
executor (TASK-124 stage A) — the planner-registry kind that previously had
none at all (see :mod:`pypeeker.refactor.edits`'s pre-stage-A stub, now
dispatching here). Same anchoring: the target is re-located by symbol id
through the CURRENT index (never through offsets captured at detection time),
and the deletion span is derived from the symbol's scope (definition line
through its last line, plus trailing blank lines) after two conservative
refusals: decorated definitions, and a last scope line carrying trailing
non-comment content.

Unlike the fix, which received ``file_path`` directly from the
:class:`~pypeeker.check.models.Violation` that carried it,
:class:`~pypeeker.intents.DeleteSymbolIntent` only carries a bare
``symbol_id`` — the shape it already had before this stage (its footprint
resolves the file the same way). :meth:`DeleteSymbolPlanner.plan` resolves
the owning file itself, project-wide, via
:class:`~pypeeker.query.SemanticQueryEngine`, before running the fix's
original hash-verified re-anchoring against that file.

The ``"delete-symbol"`` materializer itself (the ``@register_planner``
registration) stays in :mod:`pypeeker.refactor.edits`, alongside ``"edit"``
— this module only supplies the planner it now dispatches to.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pypeeker.intents import SymbolAnchor
from pypeeker.models import (
    EditEntry,
    EditOp,
    SymbolKind,
    TransactionHeader,
    TransactionSummary,
)
from pypeeker.query import SemanticQueryEngine
from pypeeker.refactor.text_anchor import (
    AnchorStateError,
    current_state,
    is_definition_header,
    line_end,
    line_start_offsets,
)
from pypeeker.storage import IndexStore, TransactionStore

_DEFINITION_KINDS = (SymbolKind.FUNCTION, SymbolKind.CLASS)


class DeleteSymbolError(Exception):
    """Raised when a delete-symbol plan cannot be created.

    ``code`` is the stable refusal slug the superseded
    ``DeleteUnusedSymbolFix`` used for the same refusal
    (``"stale-index"`` / ``"text-mismatch"`` / ``"ambiguous"`` /
    ``"file-missing"``) — TASK-124 stage B's ``check --fix`` report contract
    depends on this mapping surviving the port.
    """

    def __init__(self, code: str, message: str) -> None:
        """Store the machine code alongside the human-readable message."""
        super().__init__(message)
        self.code = code


class DeleteSymbolPlanner:
    """Delete an unreferenced module-level FUNCTION/CLASS definition."""

    def __init__(
        self, index_store: IndexStore, transaction_store: TransactionStore
    ) -> None:
        self._index_store = index_store
        self._transaction_store = transaction_store
        self._engine = SemanticQueryEngine(index_store)

    def plan(self, anchor: SymbolAnchor) -> TransactionSummary:
        """Re-locate ``anchor``'s definition via the current index and delete its span."""
        symbol_id = anchor.symbol_id
        matches = [
            s for s in self._engine.find_symbol(symbol_id) if s.kind in _DEFINITION_KINDS
        ]
        if len(matches) > 1:
            raise DeleteSymbolError(
                "ambiguous",
                f"symbol '{symbol_id}' resolves to more than one definition",
            )
        if not matches:
            raise DeleteSymbolError(
                "text-mismatch", f"symbol '{symbol_id}' is no longer in the index"
            )
        file_path = matches[0].location.file_path

        try:
            content, index = current_state(self._index_store, file_path)
        except AnchorStateError as error:
            raise DeleteSymbolError(error.code, str(error)) from error

        symbol = next(
            (
                s
                for s in index.symbols
                if s.symbol_id == symbol_id and s.kind in _DEFINITION_KINDS
            ),
            None,
        )
        if symbol is None:
            raise DeleteSymbolError(
                "text-mismatch", f"symbol '{symbol_id}' is no longer in the index"
            )
        name = symbol.name
        if symbol.decorators:
            raise DeleteSymbolError(
                "ambiguous",
                f"'{name}' is decorated; decorator lines sit above the "
                "scope span and are not deleted",
            )
        scope = next((sc for sc in index.scopes if sc.scope_id == symbol_id), None)
        if scope is None:
            raise DeleteSymbolError(
                "text-mismatch", f"no scope recorded for '{symbol_id}'"
            )

        line_starts = line_start_offsets(content)
        if scope.span.end.line >= len(line_starts):
            raise DeleteSymbolError("text-mismatch", "indexed scope span is out of range")
        start = line_starts[scope.span.start.line]

        # Text anchor: the first deleted line must be the expected header.
        header = content[start : line_end(line_starts, content, scope.span.start.line)]
        if not is_definition_header(header, symbol.kind.value, name):
            raise DeleteSymbolError(
                "text-mismatch",
                f"expected a '{symbol.kind.value} {name}' header at the "
                "indexed definition line",
            )

        # The last scope line must hold nothing after the span end except
        # whitespace or a comment — anything else would be deleted too.
        end_line = scope.span.end.line
        span_end = line_starts[end_line] + scope.span.end.column
        tail = content[span_end : line_end(line_starts, content, end_line)]
        if tail.strip() and not tail.lstrip().startswith(b"#"):
            raise DeleteSymbolError(
                "ambiguous", "the definition's last line carries trailing code"
            )
        end = (
            line_starts[end_line + 1] if end_line + 1 < len(line_starts) else len(content)
        )
        # Eat trailing blank lines up to the next non-blank line.
        for next_line in range(end_line + 1, len(line_starts)):
            next_end = line_end(line_starts, content, next_line)
            if content[line_starts[next_line] : next_end].strip():
                break
            end = (
                line_starts[next_line + 1]
                if next_line + 1 < len(line_starts)
                else len(content)
            )

        file_hash = IndexStore.compute_file_hash(self._index_store.project_root / file_path)
        edit = EditEntry(
            op=EditOp.DELETE,
            file=file_path,
            start=start,
            end=end,
            old=content[start:end].decode("utf-8"),
            new="",
            file_hash=file_hash,
        )
        tx_id = uuid.uuid4().hex[:12]
        header_meta = TransactionHeader(
            tx_id=tx_id,
            symbol_id=symbol_id,
            old_name=name,
            new_name="",
            created_at=datetime.now(timezone.utc).isoformat(),
            operation="delete-symbol",
        )
        self._transaction_store.save(header_meta, [edit], None)
        return TransactionSummary(
            tx_id=tx_id,
            operation="delete-symbol",
            symbol_id=symbol_id,
            old_name=name,
            new_name="",
            files_affected=[file_path],
            edit_count=1,
            created_at=header_meta.created_at,
        )


__all__ = ["DeleteSymbolError", "DeleteSymbolPlanner"]
