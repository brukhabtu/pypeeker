"""``replace-text`` planner (TASK-124 stage A).

Ports the superseded fix protocol's ``ReplaceTextFix`` verbatim as
:class:`~pypeeker.intents.ReplaceTextIntent`'s executor. Like the fix it
ports, it is the **reference** text-anchored op — the minimal shape of a
transform with no symbol to anchor on — and no builtin rule attaches it: the
repairs rules propose today are all index-anchored, which is what lets them
refuse with ``"stale-index"`` (see
:mod:`pypeeker.refactor.docstring_ops`, whose docstring-param rename is
symbol-anchored for exactly that reason). This planner deliberately keeps the
fix's weaker, text-only guarantee, described below.

Anchored on the location where the rule saw ``old_text`` (0-indexed
``line``/``column``, byte column, matching index conventions) plus the
expected text itself. At plan time it re-reads the file and re-resolves the
anchor:

1. If ``old_text`` still sits exactly at ``(line, column)``, plan there.
2. Otherwise — the file changed since detection — fall back to searching the
   current bytes: a *unique* occurrence of ``old_text`` re-anchors the edit
   (benign unrelated edits re-plan fine); zero occurrences decline with
   ``"text-mismatch"``; multiple occurrences decline with ``"ambiguous"``.

Offsets and the ``file_hash`` are always computed from the bytes read at plan
time, never cached from detection.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator

from pypeeker.intents import Intent, RangeAnchor, ReplaceTextIntent
from pypeeker.models import EditEntry, EditOp, TransactionHeader, TransactionSummary
from pypeeker.refactor.preconditions import (
    AnchorFileExists,
    OccurrenceExists,
    Precondition,
    UniqueOccurrence,
    evaluate_in_order,
)
from pypeeker.refactor.registry import (
    Materialized,
    MaterializeError,
    load_transaction,
    register_planner,
)
from pypeeker.refactor.text_anchor import position_to_byte_offset
from pypeeker.storage import IndexStore, TransactionStore


class ReplaceTextError(Exception):
    """Raised when a replace-text plan cannot be created.

    ``code`` is the stable refusal slug the superseded ``ReplaceTextFix``
    used for the same refusal, which ``check --fix`` still reports verbatim.
    It is derived, never hardcoded at the raise site, from the failing
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
class _ReplaceTextState:
    """Values computed while evaluating preconditions, reused to build the edit."""

    content: bytes = b""
    start: int = 0


class ReplaceTextPlanner:
    """Replace one occurrence of known text, anchored on a file position."""

    def __init__(
        self, index_store: IndexStore, transaction_store: TransactionStore
    ) -> None:
        self._index_store = index_store
        self._transaction_store = transaction_store

    def plan(self, anchor: RangeAnchor, old_text: str, new_text: str) -> TransactionSummary:
        """Plan the replacement against current bytes; verify or re-anchor."""
        file_path = anchor.file_path
        state = _ReplaceTextState()
        evaluated, failure = evaluate_in_order(
            self._iter_preconditions(state, anchor, old_text, file_path)
        )
        if failure is not None:
            failing = evaluated[-1]
            raise ReplaceTextError(failing.slug, failure.reason, precondition=failing.name)

        old_bytes = old_text.encode("utf-8")
        edit = EditEntry(
            op=EditOp.REPLACE,
            file=file_path,
            start=state.start,
            end=state.start + len(old_bytes),
            old=old_text,
            new=new_text,
            file_hash=self._index_store.file_hash(file_path),
        )
        tx_id = uuid.uuid4().hex[:12]
        header = TransactionHeader(
            tx_id=tx_id,
            symbol_id="",
            old_name=old_text,
            new_name=new_text,
            created_at=datetime.now(timezone.utc).isoformat(),
            operation="replace-text",
        )
        self._transaction_store.save(header, [edit], None)
        return TransactionSummary(
            tx_id=tx_id,
            operation="replace-text",
            symbol_id="",
            old_name=old_text,
            new_name=new_text,
            files_affected=[file_path],
            edit_count=1,
            created_at=header.created_at,
        )

    def _iter_preconditions(
        self,
        state: _ReplaceTextState,
        anchor: RangeAnchor,
        old_text: str,
        file_path: str,
    ) -> Iterator[Precondition]:
        """Yield this replacement's preconditions in evaluation order.

        The consumer must evaluate each yielded precondition before advancing
        (see :func:`~pypeeker.refactor.preconditions.evaluate_in_order`).
        When the recorded ``(line, column)`` still holds ``old_text``
        verbatim, that byte offset is used directly and generation stops
        there (a benign re-plan, not a re-anchor); otherwise re-anchoring
        falls back to the unique-occurrence checks. Either way the resolved
        start offset is stashed on ``state`` for :meth:`plan`.
        """
        yield AnchorFileExists(self._index_store, file_path)
        content = self._index_store.read_file(file_path)
        state.content = content
        old_bytes = old_text.encode("utf-8")

        offset = position_to_byte_offset(content, anchor.line, anchor.column)
        if offset is not None and content[offset : offset + len(old_bytes)] == old_bytes:
            state.start = offset
            return

        # The recorded location no longer holds the text: re-anchor only if
        # the expected text occurs exactly once in the current file.
        exists = OccurrenceExists(content, old_bytes, old_text, file_path)
        yield exists
        yield UniqueOccurrence(content, old_bytes, exists.first, old_text, file_path)
        state.start = exists.first


@register_planner(ReplaceTextIntent.kind)
def _materialize_replace_text(
    intent: Intent, store: IndexStore, tx_store: TransactionStore
) -> Materialized | str:
    """Re-plan a :class:`ReplaceTextIntent` against ``store`` (batch materializer)."""
    assert isinstance(intent, ReplaceTextIntent)
    try:
        summary = ReplaceTextPlanner(store, tx_store).plan(
            intent.anchor, intent.old_text, intent.new_text
        )
    except ReplaceTextError as error:
        return MaterializeError(
            str(error), code=error.code, precondition=error.precondition
        )
    materialized = load_transaction(tx_store, summary.tx_id)
    materialized.summary = summary
    return materialized
