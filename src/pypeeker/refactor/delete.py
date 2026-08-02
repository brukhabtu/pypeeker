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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator

from pypeeker.intents import SymbolAnchor
from pypeeker.models import (
    EditEntry,
    EditOp,
    Scope,
    Symbol,
    SymbolKind,
    TransactionHeader,
    TransactionSummary,
)
from pypeeker.query import SemanticQueryEngine
from pypeeker.refactor.preconditions import (
    AnchorFileExists,
    AnchorIndexFresh,
    DeletableScope,
    Precondition,
    ScopeSpanClean,
    SourceIsUtf8,
    SymbolMatchFound,
    SymbolMatchUnambiguous,
    UndecoratedDefinition,
    evaluate_in_order,
)
from pypeeker.refactor.text_anchor import line_end
from pypeeker.storage import IndexStore, TransactionStore

_DEFINITION_KINDS = (SymbolKind.FUNCTION, SymbolKind.CLASS)


class DeleteSymbolError(Exception):
    """Raised when a delete-symbol plan cannot be created.

    ``code`` is the stable refusal slug the superseded
    ``DeleteUnusedSymbolFix`` used for the same refusal
    (``"stale-index"`` / ``"text-mismatch"`` / ``"ambiguous"`` /
    ``"file-missing"``) — TASK-124 stage B's ``check --fix`` report contract
    depends on this mapping surviving the port. It is derived, never
    hardcoded at the raise site, from the failing
    :class:`~pypeeker.refactor.preconditions.Precondition`'s
    :attr:`~pypeeker.refactor.preconditions.Precondition.slug`; that
    precondition's :attr:`~pypeeker.refactor.preconditions.Precondition.name`
    is carried alongside as ``precondition`` (TASK-125, additive).

    ``code`` is ``None`` when the failing precondition carries no legacy
    slug — the only case today is
    :class:`~pypeeker.refactor.preconditions.SourceIsUtf8` at the decode
    guard (TASK-141), where ``check --fix`` reports the CLI's generic
    ``"plan-refused"`` and ``detail`` names the undecodable byte.
    """

    def __init__(
        self, code: str | None, message: str, *, precondition: str | None = None
    ) -> None:
        """Store the machine code alongside the human-readable message."""
        super().__init__(message)
        self.code = code
        self.precondition = precondition


@dataclass
class _DeleteSymbolState:
    """Values computed while evaluating preconditions, reused to build the edit."""

    file_path: str = ""
    content: bytes = b""
    symbol: Symbol | None = None
    scope: Scope | None = None
    line_starts: list[int] = field(default_factory=list)
    start: int = 0


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
        state = _DeleteSymbolState()
        evaluated, failure = evaluate_in_order(self._iter_preconditions(state, symbol_id))
        if failure is not None:
            failing = evaluated[-1]
            raise DeleteSymbolError(failing.slug, failure.reason, precondition=failing.name)

        content = state.content
        symbol = state.symbol
        name = symbol.name
        line_starts = state.line_starts
        scope = state.scope

        # The last scope line must hold nothing after the span end except
        # whitespace or a comment — anything else would be deleted too
        # (verified by ScopeSpanClean, evaluated as part of the precondition
        # set above); this only computes where the deletion actually ends.
        end_line = scope.span.end.line
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

        # EditEntry carries ``old`` as ``str``, so the deletion span must
        # decode before it can be recorded at all. The guard is scoped to
        # exactly that span — NOT the whole file — so an ASCII dead
        # definition inside a file holding undecodable bytes elsewhere (a
        # latin-1 comment on some other line) stays deletable, the same
        # span-scoping extract-variable and remove-import use. ``byte_offset``
        # keeps the reported byte file-absolute (TASK-141).
        guard = SourceIsUtf8(
            content[state.start : end], state.file_path, byte_offset=state.start
        )
        guard_result = guard.evaluate()
        if not guard_result.ok:
            raise DeleteSymbolError(
                guard.slug, guard_result.reason, precondition=guard.name
            )

        file_hash = self._index_store.file_hash(state.file_path)
        edit = EditEntry(
            op=EditOp.DELETE,
            file=state.file_path,
            start=state.start,
            end=end,
            old=guard.text,
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
            files_affected=[state.file_path],
            edit_count=1,
            created_at=header_meta.created_at,
        )

    def _iter_preconditions(
        self, state: _DeleteSymbolState, symbol_id: str
    ) -> Iterator[Precondition]:
        """Yield this deletion's preconditions in evaluation order.

        The consumer must evaluate each yielded precondition before advancing
        (see :func:`~pypeeker.refactor.preconditions.evaluate_in_order`); the
        resolved file path, current bytes, symbol, scope, line starts and the
        definition's start offset are stashed on ``state`` for :meth:`plan`.
        """
        matches = [
            s for s in self._engine.find_symbol(symbol_id) if s.kind in _DEFINITION_KINDS
        ]
        yield SymbolMatchUnambiguous(
            symbol_id, matches, noun="symbol", resolves_to="definition"
        )
        found = SymbolMatchFound(symbol_id, matches, noun="symbol")
        yield found
        state.file_path = found.symbol.location.file_path

        yield AnchorFileExists(self._index_store, state.file_path)
        index_fresh = AnchorIndexFresh(self._index_store, state.file_path)
        yield index_fresh
        state.content = index_fresh.content

        fresh_matches = [
            s
            for s in index_fresh.index.symbols
            if s.symbol_id == symbol_id and s.kind in _DEFINITION_KINDS
        ]
        still = SymbolMatchFound(symbol_id, fresh_matches, noun="symbol")
        yield still
        state.symbol = still.symbol

        yield UndecoratedDefinition(state.symbol)

        scope_check = DeletableScope(index_fresh.index, state.content, state.symbol)
        yield scope_check
        state.scope = scope_check.scope
        state.line_starts = scope_check.line_starts
        state.start = scope_check.start

        yield ScopeSpanClean(state.content, state.line_starts, state.scope)


__all__ = ["DeleteSymbolError", "DeleteSymbolPlanner"]
