"""``rename-docstring-param`` planner (TASK-124 stage A).

Ports the superseded ``docstring-drift`` autofix
(``_DocstringParamRenameFix``, TASK-93's repair on TASK-82's fix protocol)
verbatim in behavior as
:class:`~pypeeker.intents.RenameDocstringParamIntent`'s executor.

Anchored on the FUNCTION/METHOD symbol id plus the docstring style the rule
detected (or was told to force). :meth:`DocstringParamRenamePlanner.plan`
follows the index-anchored discipline every other ported planner shares (see
:mod:`pypeeker.refactor.text_anchor`): it re-reads the file, refuses on a
stale index (``"stale-index"``), re-locates the symbol and **re-derives the
drift from the CURRENT docstring** — never from detection-time offsets — and
proceeds only while the rename shape still holds: exactly one
documented-but-absent name (``old_param``) and exactly one undocumented
signature parameter (``new_param``).

The docstring region is re-located textually: the indexed docstring text (the
first string in the def body, triple-quote-stripped) must occur exactly once
inside the function's scope span, and ``old_param`` must occur exactly once as
a bare name token inside that region (not preceded by ``*`` or a word
character). One REPLACE edit covering just the name token is emitted; every
other case declines (``"ambiguous"`` for plural candidates, ``"text-mismatch"``
when the anchor is gone).

Unlike the fix, which received ``file_path`` directly from the
:class:`~pypeeker.check.models.Violation` that carried it,
:class:`~pypeeker.intents.RenameDocstringParamIntent` carries only the symbol
id, so the planner resolves the owning file project-wide through
:class:`~pypeeker.query.SemanticQueryEngine` first — the same shape
:class:`~pypeeker.refactor.delete.DeleteSymbolPlanner` uses.

The docstring parsers themselves live in :mod:`pypeeker.analysis.docstrings`,
shared with the ``docstring-drift`` rule that emits this intent: ``check`` and
``refactor`` may not import each other, so re-deriving the drift here uses the
identical parser the rule detected it with.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator

from pypeeker.intents import Intent, RenameDocstringParamIntent, SymbolAnchor
from pypeeker.models import (
    EditEntry,
    EditOp,
    Symbol,
    SymbolKind,
    TransactionHeader,
    TransactionSummary,
)
from pypeeker.query import SemanticQueryEngine
from pypeeker.refactor.preconditions import (
    AnchorFileExists,
    AnchorIndexFresh,
    DocstringScopeLocated,
    DocstringStillPresent,
    DocstringTextFound,
    DocstringTextUnique,
    DocstringTokenFound,
    DocstringTokenUnique,
    DocumentedParamDriftMatches,
    DocumentedParamDriftSingle,
    ParamsSectionPresent,
    Precondition,
    SymbolMatchFound,
    SymbolMatchUnambiguous,
    evaluate_in_order,
)
from pypeeker.refactor.registry import (
    Materialized,
    MaterializeError,
    load_transaction,
    register_planner,
)
from pypeeker.storage import IndexStore, TransactionStore

_FUNCTION_KINDS = (SymbolKind.FUNCTION, SymbolKind.METHOD)


class DocstringParamRenameError(Exception):
    """Raised when a docstring-param rename cannot be planned.

    ``code`` is the stable refusal slug the superseded
    ``_DocstringParamRenameFix`` used for the same refusal
    (``"stale-index"`` / ``"text-mismatch"`` / ``"ambiguous"`` /
    ``"file-missing"``) — TASK-124 stage B's ``check --fix`` report contract
    depends on this mapping surviving the port. It is derived, never
    hardcoded at the raise site, from the failing
    :class:`~pypeeker.refactor.preconditions.Precondition`'s
    :attr:`~pypeeker.refactor.preconditions.Precondition.slug`; that
    precondition's :attr:`~pypeeker.refactor.preconditions.Precondition.name`
    is carried alongside as ``precondition`` (TASK-125, additive).
    """

    def __init__(
        self, code: str, message: str, *, precondition: str | None = None
    ) -> None:
        """Store the machine code alongside the human-readable message."""
        super().__init__(message)
        self.code = code
        self.precondition = precondition


@dataclass
class _DocstringParamRenameState:
    """Values computed while evaluating preconditions, reused to build the edit."""

    file_path: str = ""
    symbol: Symbol | None = None
    token_start: int = 0


class DocstringParamRenamePlanner:
    """Rewrite one stale documented parameter name inside a docstring."""

    def __init__(
        self, index_store: IndexStore, transaction_store: TransactionStore
    ) -> None:
        self._index_store = index_store
        self._transaction_store = transaction_store
        self._engine = SemanticQueryEngine(index_store)

    def plan(
        self, anchor: SymbolAnchor, old_param: str, new_param: str, style: str
    ) -> TransactionSummary:
        """Re-derive the drift from the current index and rewrite the token."""
        symbol_id = anchor.symbol_id
        state = _DocstringParamRenameState()
        evaluated, failure = evaluate_in_order(
            self._iter_preconditions(state, symbol_id, old_param, new_param, style)
        )
        if failure is not None:
            failing = evaluated[-1]
            raise DocstringParamRenameError(
                failing.slug, failure.reason, precondition=failing.name
            )

        old_param_bytes = old_param.encode("utf-8")
        edit = EditEntry(
            op=EditOp.REPLACE,
            file=state.file_path,
            start=state.token_start,
            end=state.token_start + len(old_param_bytes),
            old=old_param,
            new=new_param,
            file_hash=self._index_store.file_hash(state.file_path),
        )
        tx_id = uuid.uuid4().hex[:12]
        header = TransactionHeader(
            tx_id=tx_id,
            symbol_id=symbol_id,
            old_name=old_param,
            new_name=new_param,
            created_at=datetime.now(timezone.utc).isoformat(),
            operation="rename-docstring-param",
        )
        self._transaction_store.save(header, [edit], None)
        return TransactionSummary(
            tx_id=tx_id,
            operation="rename-docstring-param",
            symbol_id=symbol_id,
            old_name=old_param,
            new_name=new_param,
            files_affected=[state.file_path],
            edit_count=1,
            created_at=header.created_at,
        )

    def _iter_preconditions(
        self,
        state: _DocstringParamRenameState,
        symbol_id: str,
        old_param: str,
        new_param: str,
        style: str,
    ) -> Iterator[Precondition]:
        """Yield this rename's preconditions in evaluation order.

        The consumer must evaluate each yielded precondition before advancing
        (see :func:`~pypeeker.refactor.preconditions.evaluate_in_order`); the
        resolved file path, current symbol and the rewritten token's start
        offset are stashed on ``state`` for :meth:`plan`.
        """
        matches = [
            s for s in self._engine.find_symbol(symbol_id) if s.kind in _FUNCTION_KINDS
        ]
        yield SymbolMatchUnambiguous(
            symbol_id, matches, noun="symbol", resolves_to="definition"
        )
        owning = SymbolMatchFound(symbol_id, matches, noun="function")
        yield owning
        state.file_path = owning.symbol.location.file_path

        yield AnchorFileExists(self._index_store, state.file_path)
        index_fresh = AnchorIndexFresh(self._index_store, state.file_path)
        yield index_fresh

        fresh_matches = [
            s
            for s in index_fresh.index.symbols
            if s.symbol_id == symbol_id and s.kind in _FUNCTION_KINDS
        ]
        still = SymbolMatchFound(symbol_id, fresh_matches, noun="function")
        yield still
        state.symbol = still.symbol

        yield DocstringStillPresent(symbol_id, state.symbol)

        section_check = ParamsSectionPresent(state.symbol.docstring, style)
        yield section_check

        drift = DocumentedParamDriftSingle(
            section_check.section, index_fresh.index, state.symbol
        )
        yield drift

        yield DocumentedParamDriftMatches(drift.ghosts, drift.missing, old_param, new_param)

        scope_check = DocstringScopeLocated(index_fresh.index, index_fresh.content, symbol_id)
        yield scope_check

        doc_bytes = state.symbol.docstring.encode("utf-8")
        text_found = DocstringTextFound(scope_check.region, doc_bytes)
        yield text_found
        yield DocstringTextUnique(scope_check.region, doc_bytes, text_found.first)

        doc_start = scope_check.region_start + text_found.first
        token = re.compile(
            rb"(?<![\w*])" + re.escape(old_param.encode("utf-8")) + rb"(?!\w)"
        )
        token_found = DocstringTokenFound(doc_bytes, token, old_param)
        yield token_found
        yield DocstringTokenUnique(token_found.matches, old_param)

        state.token_start = doc_start + token_found.matches[0].start()


@register_planner(RenameDocstringParamIntent.kind)
def _materialize_rename_docstring_param(
    intent: Intent, store: IndexStore, tx_store: TransactionStore
) -> Materialized | str:
    """Re-plan a :class:`RenameDocstringParamIntent` against ``store``."""
    assert isinstance(intent, RenameDocstringParamIntent)
    try:
        summary = DocstringParamRenamePlanner(store, tx_store).plan(
            intent.anchor, intent.old_param, intent.new_param, intent.style
        )
    except DocstringParamRenameError as error:
        return MaterializeError(str(error), code=error.code, precondition=error.precondition)
    materialized = load_transaction(tx_store, summary.tx_id)
    materialized.summary = summary
    return materialized
