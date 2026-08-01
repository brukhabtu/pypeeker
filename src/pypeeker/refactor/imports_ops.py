"""``remove-import`` / ``rewrite-star-import`` planners (TASK-124 stage A).

Ports the superseded ``unused-imports`` autofix and
``star-imports`` autofix verbatim in behavior as
:class:`~pypeeker.intents.RemoveImportIntent`'s and
:class:`~pypeeker.intents.RewriteStarImportIntent`'s executors. Both re-locate
their target IMPORT symbol by id through the CURRENT index (never through
offsets captured at detection time) before touching bytes, and both decline
the exact conditions their fix ancestors did — see each planner's docstring.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator

from pypeeker.intents import Intent, RemoveImportIntent, RewriteStarImportIntent, SymbolAnchor
from pypeeker.models import (
    EditEntry,
    EditOp,
    FileIndex,
    Symbol,
    SymbolKind,
    TransactionHeader,
    TransactionSummary,
    is_unresolved_attr,
)
from pypeeker.query import SemanticQueryEngine
from pypeeker.refactor.preconditions import (
    AnchorFileExists,
    AnchorIndexFresh,
    AnchorTextMatches,
    ImportLineInRange,
    ImportLineSurgerySafe,
    ImportNameUnambiguousOnLine,
    ImportSegmentsLocatable,
    ImportStatementLine,
    Precondition,
    SingleStarImportInFile,
    StarAttributionUnambiguous,
    StarLinePlainForm,
    StarSupplyNonEmpty,
    StarTargetModuleIndexed,
    StarTokenMatches,
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
from pypeeker.refactor.text_anchor import position_to_byte_offset
from pypeeker.storage import IndexStore, TransactionStore

# The bytes that must precede the ``*`` token on its line for the rewrite to
# apply: a plain single-line ``from <module> import *`` statement (relative
# dots allowed). Anything else — continuations, parentheses, a
# semicolon-joined statement — fails verification and declines.
_STAR_LINE_PREFIX = re.compile(rb"\s*from\s+[.\w]+\s+import\s+$")


class RemoveImportError(Exception):
    """Raised when a remove-import plan cannot be created.

    ``code`` is the stable refusal slug the superseded
    ``RemoveUnusedImportFix`` used for the same refusal, which
    ``check --fix`` still reports verbatim. It is derived, never hardcoded
    at the raise site, from the failing
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


class RewriteStarImportError(Exception):
    """Raised when a rewrite-star-import plan cannot be created.

    ``code`` is the stable refusal slug the
    superseded ``_RewriteStarImportFix`` used for the same refusal, derived
    the same way (and with the same ``precondition`` companion field) as
    :class:`RemoveImportError`.
    """

    def __init__(
        self, code: str, message: str, *, precondition: str | None = None
    ) -> None:
        """Store the machine code alongside the human-readable message."""
        super().__init__(message)
        self.code = code
        self.precondition = precondition


@dataclass(frozen=True)
class _ImportSegment:
    """One comma-separated entry on an import line.

    ``text_start``/``text_end`` are byte columns of the stripped entry text
    within the line; ``bound_name`` is the local name the entry binds (the
    alias after ``as`` when present, the first dotted segment for a plain
    ``import a.b``, the entry itself for a ``from`` import).
    """

    text_start: int
    text_end: int
    bound_name: bytes


def _import_name_segments(
    body: bytes, is_from_import: bool
) -> list[_ImportSegment] | None:
    """Split the names part of a single-line import into segments.

    ``body`` is the physical line without its newline. Returns ``None`` when
    the ``import`` keyword cannot be located (not expected for indexed import
    symbols, but anchors are verified, never assumed).
    """
    if is_from_import:
        marker = b" import "
        at = body.find(marker)
        if at == -1:
            return None
        names_start = at + len(marker)
    else:
        indent = len(body) - len(body.lstrip())
        if not body.lstrip().startswith(b"import "):
            return None
        names_start = indent + len(b"import ")

    segments: list[_ImportSegment] = []
    part_start = names_start
    for part in body[names_start:].split(b","):
        stripped = part.strip()
        text_start = part_start + (len(part) - len(part.lstrip()))
        text_end = text_start + len(stripped)
        if b" as " in stripped:
            bound = stripped.rsplit(b" as ", 1)[1].strip()
        elif is_from_import:
            bound = stripped
        else:
            bound = stripped.split(b".", 1)[0]
        segments.append(_ImportSegment(text_start, text_end, bound))
        part_start += len(part) + 1  # +1 for the comma
    return segments


@dataclass
class _RemoveImportState:
    """Values computed while evaluating preconditions, reused to build the edit."""

    file_path: str = ""
    content: bytes = b""
    symbol: Symbol | None = None
    line_start: int = 0
    line_stop: int = 0
    line: bytes = b""
    body: bytes = b""
    segments: list[_ImportSegment] | None = None
    index_matches: list[int] = field(default_factory=list)


class RemoveImportPlanner:
    """Delete an unused import binding, anchored on its IMPORT symbol id.

    Re-locates the bound name token via the current index and edits the
    physical import line. A single-name line (``import x`` / ``from m import
    x`` / ``import x as y``) is deleted whole, including its newline; on a
    multi-name line only the matching name entry and its adjacent comma are
    deleted.

    Conservative declines (``"ambiguous"``): parenthesized import lists,
    backslash-continued lines, and multi-name lines where the bound name
    cannot be matched to exactly one comma-separated entry (e.g. a trailing
    comment glued to the entry).
    """

    def __init__(
        self, index_store: IndexStore, transaction_store: TransactionStore
    ) -> None:
        self._index_store = index_store
        self._transaction_store = transaction_store
        self._engine = SemanticQueryEngine(index_store)

    def plan(self, anchor: SymbolAnchor, name: str) -> TransactionSummary:
        """Re-locate the import via the current index and delete it."""
        symbol_id = anchor.symbol_id
        state = _RemoveImportState()
        evaluated, failure = evaluate_in_order(
            self._iter_preconditions(state, symbol_id, name)
        )
        if failure is not None:
            failing = evaluated[-1]
            raise RemoveImportError(failing.slug, failure.reason, precondition=failing.name)

        segments = state.segments
        file_hash = self._index_store.file_hash(state.file_path)
        if len(segments) == 1:
            # Single-name import: delete the whole line, newline included.
            edit = EditEntry(
                op=EditOp.DELETE,
                file=state.file_path,
                start=state.line_start,
                end=state.line_stop,
                old=state.line.decode("utf-8"),
                new="",
                file_hash=file_hash,
            )
        else:
            i = state.index_matches[0]
            if i + 1 < len(segments):
                # Not the last entry: delete from its text through the start
                # of the next entry's text (covers the comma and following space).
                del_start, del_end = segments[i].text_start, segments[i + 1].text_start
            else:
                # Last entry: delete the preceding comma run through its text end.
                del_start, del_end = segments[i - 1].text_end, segments[i].text_end
            edit = EditEntry(
                op=EditOp.DELETE,
                file=state.file_path,
                start=state.line_start + del_start,
                end=state.line_start + del_end,
                old=state.body[del_start:del_end].decode("utf-8"),
                new="",
                file_hash=file_hash,
            )

        tx_id = uuid.uuid4().hex[:12]
        header = TransactionHeader(
            tx_id=tx_id,
            symbol_id=symbol_id,
            old_name=name,
            new_name="",
            created_at=datetime.now(timezone.utc).isoformat(),
            operation="remove-import",
        )
        self._transaction_store.save(header, [edit], None)
        return TransactionSummary(
            tx_id=tx_id,
            operation="remove-import",
            symbol_id=symbol_id,
            old_name=name,
            new_name="",
            files_affected=[state.file_path],
            edit_count=1,
            created_at=header.created_at,
        )

    def _iter_preconditions(
        self, state: _RemoveImportState, symbol_id: str, name: str
    ) -> Iterator[Precondition]:
        """Yield this removal's preconditions in evaluation order.

        The consumer must evaluate each yielded precondition before advancing
        (see :func:`~pypeeker.refactor.preconditions.evaluate_in_order`); the
        resolved file path, current bytes, symbol, physical line and its
        parsed comma-separated segments are stashed on ``state`` for
        :meth:`plan`.
        """
        matches = [
            s
            for s in self._engine.find_symbol(symbol_id)
            if s.kind is SymbolKind.IMPORT
        ]
        yield SymbolMatchUnambiguous(symbol_id, matches, noun="import")
        found = SymbolMatchFound(symbol_id, matches, noun="import")
        yield found
        state.file_path = found.symbol.location.file_path

        yield AnchorFileExists(self._index_store, state.file_path)
        index_fresh = AnchorIndexFresh(self._index_store, state.file_path)
        yield index_fresh
        state.content = index_fresh.content

        fresh_matches = [
            s
            for s in index_fresh.index.symbols
            if s.symbol_id == symbol_id and s.kind is SymbolKind.IMPORT
        ]
        still = SymbolMatchFound(symbol_id, fresh_matches, noun="import")
        yield still
        state.symbol = still.symbol

        line_check = ImportLineInRange(
            state.content, state.symbol.location.span.start.line
        )
        yield line_check
        state.line_start = line_check.line_start
        state.line_stop = line_check.line_stop
        state.line = line_check.line
        state.body = line_check.body

        column = state.symbol.location.span.start.column
        yield AnchorTextMatches(state.body, column, name)

        yield ImportStatementLine(state.body)
        yield ImportLineSurgerySafe(state.body)

        stripped = state.body.lstrip()
        segments = _import_name_segments(state.body, stripped.startswith(b"from "))
        yield ImportSegmentsLocatable(segments)
        state.segments = segments

        if len(segments) != 1:
            name_bytes = name.encode("utf-8")
            index_matches = [
                i for i, seg in enumerate(segments) if seg.bound_name == name_bytes
            ]
            yield ImportNameUnambiguousOnLine(name, index_matches)
            state.index_matches = index_matches


# ── shared derivation helpers for RewriteStarImportPlanner ──────────────────
# Ported verbatim from check.builtin.star_imports — see that module's
# docstring for the first-star-wins attribution model and its confidence
# consequences.


def _star_symbols(index: FileIndex) -> list[Symbol]:
    """The file's ``"*"`` IMPORT symbols, in file order."""
    stars = [s for s in index.symbols if s.kind is SymbolKind.IMPORT and s.name == "*"]
    stars.sort(key=lambda s: (s.location.span.start.line, s.location.span.start.column))
    return stars


def _module_indexes(indexes: Sequence[FileIndex]) -> dict[str, FileIndex]:
    """Map each index's dotted module path to its :class:`FileIndex`."""
    out: dict[str, FileIndex] = {}
    for index in indexes:
        module_id = next((s.symbol_id for s in index.symbols if s.kind is SymbolKind.MODULE), None)
        if module_id is not None:
            out[module_id] = index
    return out


def _load_indexes(store: IndexStore, current: FileIndex) -> list[FileIndex]:
    """Every index in the store, with ``current`` standing in for its own file."""
    indexes: list[FileIndex] = []
    for path in store.list_indexed_files():
        index = current if path == current.file_path else store.load(path)
        if index is not None:
            indexes.append(index)
    return indexes


def _public_surface(index: FileIndex) -> frozenset[str]:
    """Public module-level names of ``index`` — what ``import *`` can supply."""
    module_id = next((s.symbol_id for s in index.symbols if s.kind is SymbolKind.MODULE), None)
    if module_id is None:
        return frozenset()
    return frozenset(
        s.name
        for s in index.symbols
        if s.parent_scope_id == module_id
        and s.kind is not SymbolKind.MODULE
        and s.name != "*"
        and not s.name.startswith("_")
    )


def _unresolved_bare_names(index: FileIndex) -> set[str]:
    """Bare unresolved reference names in ``index`` — star-supply candidates."""
    return {
        ref.symbol_id
        for ref in index.references
        if not ref.resolved
        and not is_unresolved_attr(ref.symbol_id)
        and ref.symbol_id.isidentifier()
        and not ref.symbol_id.startswith("_")
    }


def _attribute_names(
    stars: Sequence[Symbol],
    unresolved: set[str],
    modules: Mapping[str, FileIndex],
) -> tuple[dict[str, list[str]], list[str]]:
    """Attribute unresolved names to star imports, first-star-wins."""
    remaining = set(unresolved)
    used_by: dict[str, list[str]] = {}
    for star in stars:
        target = modules.get(star.imported_from)
        if target is None:
            continue
        supplied = sorted(remaining & _public_surface(target))
        used_by[star.symbol_id] = supplied
        remaining.difference_update(supplied)
    return used_by, sorted(remaining)


@dataclass
class _RewriteStarImportState:
    """Values computed while evaluating preconditions, reused to build the edit."""

    file_path: str = ""
    content: bytes = b""
    index: FileIndex | None = None
    star: Symbol | None = None
    offset: int = 0
    names: list[str] = field(default_factory=list)


class RewriteStarImportPlanner:
    """Rewrite ``from m import *`` to ``from m import a, b, c`` (sorted).

    Anchored on the ``"*"`` IMPORT symbol id: re-reads the file through the
    hash-verified current index, re-derives the used names from the CURRENT
    indexes in the store, text-verifies that the ``*`` token still sits on a
    plain ``from <module> import *`` line, and emits one REPLACE edit
    swapping the ``*`` for the sorted name list — indentation, the module
    text as written (relative imports stay relative), and any trailing
    comment are untouched.
    """

    def __init__(
        self, index_store: IndexStore, transaction_store: TransactionStore
    ) -> None:
        self._index_store = index_store
        self._transaction_store = transaction_store
        self._engine = SemanticQueryEngine(index_store)

    def plan(self, anchor: SymbolAnchor, module: str) -> TransactionSummary:
        """Re-derive used names from the current index and rewrite the star."""
        symbol_id = anchor.symbol_id
        state = _RewriteStarImportState()
        evaluated, failure = evaluate_in_order(
            self._iter_preconditions(state, symbol_id)
        )
        if failure is not None:
            failing = evaluated[-1]
            raise RewriteStarImportError(
                failing.slug, failure.reason, precondition=failing.name
            )

        file_hash = self._index_store.file_hash(state.file_path)
        new_names = ", ".join(state.names)
        edit = EditEntry(
            op=EditOp.REPLACE,
            file=state.file_path,
            start=state.offset,
            end=state.offset + 1,
            old="*",
            new=new_names,
            file_hash=file_hash,
        )
        tx_id = uuid.uuid4().hex[:12]
        header = TransactionHeader(
            tx_id=tx_id,
            symbol_id=symbol_id,
            old_name="*",
            new_name=new_names,
            created_at=datetime.now(timezone.utc).isoformat(),
            operation="rewrite-star-import",
        )
        self._transaction_store.save(header, [edit], None)
        return TransactionSummary(
            tx_id=tx_id,
            operation="rewrite-star-import",
            symbol_id=symbol_id,
            old_name="*",
            new_name=new_names,
            files_affected=[state.file_path],
            edit_count=1,
            created_at=header.created_at,
        )

    def _iter_preconditions(
        self, state: _RewriteStarImportState, symbol_id: str
    ) -> Iterator[Precondition]:
        """Yield this rewrite's preconditions in evaluation order.

        The consumer must evaluate each yielded precondition before advancing
        (see :func:`~pypeeker.refactor.preconditions.evaluate_in_order`); the
        resolved file path, current bytes/index, the ``"*"`` symbol, its byte
        offset and the sorted names it supplies are stashed on ``state`` for
        :meth:`plan`.
        """
        matches = [
            s
            for s in self._engine.find_symbol(symbol_id)
            if s.kind is SymbolKind.IMPORT and s.name == "*"
        ]
        yield SymbolMatchUnambiguous(symbol_id, matches, noun="star import")
        found = SymbolMatchFound(symbol_id, matches, noun="star import")
        yield found
        state.file_path = found.symbol.location.file_path

        yield AnchorFileExists(self._index_store, state.file_path)
        index_fresh = AnchorIndexFresh(self._index_store, state.file_path)
        yield index_fresh
        state.content = index_fresh.content
        state.index = index_fresh.index

        fresh_matches = [
            s
            for s in index_fresh.index.symbols
            if s.symbol_id == symbol_id and s.kind is SymbolKind.IMPORT and s.name == "*"
        ]
        still = SymbolMatchFound(symbol_id, fresh_matches, noun="star import")
        yield still
        state.star = still.symbol

        stars = _star_symbols(state.index)
        yield SingleStarImportInFile(state.file_path, stars)

        modules = _module_indexes(_load_indexes(self._index_store, state.index))
        yield StarTargetModuleIndexed(state.star.imported_from, modules)

        used_by, unattributed = _attribute_names(
            stars, _unresolved_bare_names(state.index), modules
        )
        yield StarAttributionUnambiguous(unattributed)

        names = used_by.get(state.star.symbol_id, [])
        yield StarSupplyNonEmpty(state.star.imported_from, names)
        state.names = names

        offset = position_to_byte_offset(
            state.content, state.star.location.span.start.line, state.star.location.span.start.column
        )
        yield StarTokenMatches(state.content, offset)
        state.offset = offset

        yield StarLinePlainForm(state.content, state.offset, _STAR_LINE_PREFIX)


@register_planner(RemoveImportIntent.kind)
def _materialize_remove_import(
    intent: Intent, store: IndexStore, tx_store: TransactionStore
) -> Materialized | str:
    """Re-plan a :class:`RemoveImportIntent` against ``store`` (batch materializer)."""
    assert isinstance(intent, RemoveImportIntent)
    try:
        summary = RemoveImportPlanner(store, tx_store).plan(intent.anchor, intent.name)
    except RemoveImportError as error:
        return MaterializeError(
            str(error), code=error.code, precondition=error.precondition
        )
    materialized = load_transaction(tx_store, summary.tx_id)
    materialized.summary = summary
    return materialized


@register_planner(RewriteStarImportIntent.kind)
def _materialize_rewrite_star_import(
    intent: Intent, store: IndexStore, tx_store: TransactionStore
) -> Materialized | str:
    """Re-plan a :class:`RewriteStarImportIntent` against ``store`` (batch materializer)."""
    assert isinstance(intent, RewriteStarImportIntent)
    try:
        summary = RewriteStarImportPlanner(store, tx_store).plan(intent.anchor, intent.module)
    except RewriteStarImportError as error:
        return MaterializeError(
            str(error), code=error.code, precondition=error.precondition
        )
    materialized = load_transaction(tx_store, summary.tx_id)
    materialized.summary = summary
    return materialized
