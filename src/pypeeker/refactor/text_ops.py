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
from datetime import datetime, timezone

from pypeeker.intents import Intent, RangeAnchor, ReplaceTextIntent
from pypeeker.models import EditEntry, EditOp, TransactionHeader, TransactionSummary
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
    """

    def __init__(self, code: str, message: str) -> None:
        """Store the machine code alongside the human-readable message."""
        super().__init__(message)
        self.code = code


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
        source = self._index_store.project_root / file_path
        if not source.exists():
            raise ReplaceTextError("file-missing", f"{file_path} no longer exists")
        content = source.read_bytes()
        old_bytes = old_text.encode("utf-8")

        start = self._resolve_anchor(content, anchor, old_bytes, old_text, file_path)

        edit = EditEntry(
            op=EditOp.REPLACE,
            file=file_path,
            start=start,
            end=start + len(old_bytes),
            old=old_text,
            new=new_text,
            file_hash=IndexStore.compute_file_hash(source),
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

    def _resolve_anchor(
        self,
        content: bytes,
        anchor: RangeAnchor,
        old_bytes: bytes,
        old_text: str,
        file_path: str,
    ) -> int:
        """Byte offset where ``old_text`` verifiably sits, or a decline."""
        offset = position_to_byte_offset(content, anchor.line, anchor.column)
        if offset is not None and content[offset : offset + len(old_bytes)] == old_bytes:
            return offset
        # The recorded location no longer holds the text: re-anchor only if
        # the expected text occurs exactly once in the current file.
        first = content.find(old_bytes)
        if first == -1:
            raise ReplaceTextError(
                "text-mismatch", f"expected text {old_text!r} not found in {file_path}"
            )
        if content.find(old_bytes, first + 1) != -1:
            raise ReplaceTextError(
                "ambiguous",
                f"expected text {old_text!r} occurs more than once in {file_path}",
            )
        return first


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
        return MaterializeError(str(error), code=error.code)
    materialized = load_transaction(tx_store, summary.tx_id)
    materialized.summary = summary
    return materialized
