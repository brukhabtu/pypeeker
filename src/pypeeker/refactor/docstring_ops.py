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
from datetime import datetime, timezone

from pypeeker.analysis import param_drift, parse_documented_params, signature_params
from pypeeker.intents import Intent, RenameDocstringParamIntent, SymbolAnchor
from pypeeker.models import (
    EditEntry,
    EditOp,
    FileIndex,
    SymbolKind,
    TransactionHeader,
    TransactionSummary,
)
from pypeeker.query import SemanticQueryEngine
from pypeeker.refactor.registry import (
    Materialized,
    MaterializeError,
    load_transaction,
    register_planner,
)
from pypeeker.refactor.text_anchor import (
    AnchorStateError,
    current_state,
    line_start_offsets,
)
from pypeeker.storage import IndexStore, TransactionStore

_FUNCTION_KINDS = (SymbolKind.FUNCTION, SymbolKind.METHOD)


class DocstringParamRenameError(Exception):
    """Raised when a docstring-param rename cannot be planned.

    ``code`` is the stable refusal slug the superseded
    ``_DocstringParamRenameFix`` used for the same refusal
    (``"stale-index"`` / ``"text-mismatch"`` / ``"ambiguous"`` /
    ``"file-missing"``) — TASK-124 stage B's ``check --fix`` report contract
    depends on this mapping surviving the port.
    """

    def __init__(self, code: str, message: str) -> None:
        """Store the machine code alongside the human-readable message."""
        super().__init__(message)
        self.code = code


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
        file_path = self._owning_file(symbol_id)
        try:
            content, index = current_state(self._index_store, file_path)
        except AnchorStateError as error:
            raise DocstringParamRenameError(error.code, str(error)) from error

        symbol = next(
            (
                s
                for s in index.symbols
                if s.symbol_id == symbol_id and s.kind in _FUNCTION_KINDS
            ),
            None,
        )
        if symbol is None:
            raise DocstringParamRenameError(
                "text-mismatch", f"function '{symbol_id}' is no longer in the index"
            )
        if not symbol.docstring:
            raise DocstringParamRenameError(
                "text-mismatch", f"'{symbol_id}' no longer has a docstring"
            )
        section = parse_documented_params(symbol.docstring, style)
        if section is None:
            raise DocstringParamRenameError(
                "text-mismatch",
                f"the docstring no longer has a {style}-style params section",
            )
        ghosts, missing = param_drift(section, signature_params(index, symbol))
        if len(ghosts) != 1 or len(missing) != 1:
            raise DocstringParamRenameError(
                "ambiguous",
                "the drift is no longer a single-parameter rename "
                f"({len(ghosts)} stale documented name(s), "
                f"{len(missing)} undocumented parameter(s))",
            )
        if ghosts[0] != old_param or missing[0] != new_param:
            raise DocstringParamRenameError(
                "text-mismatch",
                f"the drift changed: '{ghosts[0]}' -> '{missing[0]}' rather "
                f"than '{old_param}' -> '{new_param}'",
            )

        doc_start, doc_bytes = self._docstring_region(
            content, index, symbol_id, symbol.docstring
        )
        token = re.compile(
            rb"(?<![\w*])" + re.escape(old_param.encode("utf-8")) + rb"(?!\w)"
        )
        matches = list(token.finditer(doc_bytes))
        if not matches:
            raise DocstringParamRenameError(
                "text-mismatch",
                f"'{old_param}' does not occur as a name token in the docstring",
            )
        if len(matches) > 1:
            raise DocstringParamRenameError(
                "ambiguous",
                f"'{old_param}' occurs {len(matches)} times in the "
                "docstring; rewriting one occurrence is unsafe",
            )
        start = doc_start + matches[0].start()
        edit = EditEntry(
            op=EditOp.REPLACE,
            file=file_path,
            start=start,
            end=start + len(old_param.encode("utf-8")),
            old=old_param,
            new=new_param,
            file_hash=IndexStore.compute_file_hash(
                self._index_store.project_root / file_path
            ),
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
            files_affected=[file_path],
            edit_count=1,
            created_at=header.created_at,
        )

    def _owning_file(self, symbol_id: str) -> str:
        """The file whose index defines ``symbol_id``, or a decline."""
        matches = [
            s for s in self._engine.find_symbol(symbol_id) if s.kind in _FUNCTION_KINDS
        ]
        if len(matches) > 1:
            raise DocstringParamRenameError(
                "ambiguous",
                f"symbol '{symbol_id}' resolves to more than one definition",
            )
        if not matches:
            raise DocstringParamRenameError(
                "text-mismatch", f"function '{symbol_id}' is no longer in the index"
            )
        return matches[0].location.file_path

    def _docstring_region(
        self, content: bytes, index: FileIndex, symbol_id: str, docstring: str
    ) -> tuple[int, bytes]:
        """(byte offset, bytes) of the docstring text inside the scope span.

        The indexed docstring is the first string in the def body with its
        quotes stripped, so its text appears verbatim in the file; it must
        occur exactly once within the function's scope span to anchor safely.
        """
        scope = next((sc for sc in index.scopes if sc.scope_id == symbol_id), None)
        if scope is None:
            raise DocstringParamRenameError(
                "text-mismatch", f"no scope recorded for '{symbol_id}'"
            )
        line_starts = line_start_offsets(content)
        if scope.span.end.line >= len(line_starts):
            raise DocstringParamRenameError(
                "text-mismatch", "indexed scope span is out of range"
            )
        region_start = line_starts[scope.span.start.line]
        region_end = line_starts[scope.span.end.line] + scope.span.end.column
        region = content[region_start:region_end]

        # The indexed docstring is verified against current bytes by
        # current_state's hash check; this find re-anchors it to an offset.
        doc_bytes = docstring.encode("utf-8")
        first = region.find(doc_bytes)
        if first == -1:
            raise DocstringParamRenameError(
                "text-mismatch",
                "the docstring text was not found inside the function body",
            )
        if region.find(doc_bytes, first + 1) != -1:
            raise DocstringParamRenameError(
                "ambiguous",
                "the docstring text occurs more than once inside the function body",
            )
        start = region_start + first
        return start, content[start : start + len(doc_bytes)]


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
        return MaterializeError(str(error), code=error.code)
    materialized = load_transaction(tx_store, summary.tx_id)
    materialized.summary = summary
    return materialized
