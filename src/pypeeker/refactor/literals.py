"""``tuplify`` planner (TASK-124 stage A).

Ports the superseded ``prefer-tuple`` autofix verbatim in behavior as
:class:`~pypeeker.intents.TuplifyIntent`'s executor: rewrite a never-mutated
list literal ``[...]`` bound to a variable as a tuple ``(...)``.

Anchored on the VARIABLE symbol id: :meth:`TuplifyPlanner.plan` re-locates
the name token via the current index, expects ``name = [`` (an
inferred-list binding, i.e. a bare assignment whose RHS starts with a list
literal or comprehension), and matches the closing ``]`` with a small
byte-level bracket scanner.

An element list is tuplified by swapping the two bracket bytes: a
single-element list closes with ``,)`` so ``[x]`` becomes ``(x,)`` rather
than a parenthesized expression, while ``[]`` and any list with a top-level
comma keep a plain ``)``. A **comprehension** has no tuple-literal form, so
``[c for c in xs]`` is rewritten to ``tuple(c for c in xs)`` (never the
broken ``(c for c in xs,)``).

The scanner is deliberately conservative — strings and comments make
byte-level bracket matching fragile, so it declines (``"ambiguous"``) on
anything it cannot scan safely: f-strings (3.12+ allows same-quote nesting
inside ``{...}``), triple-quoted strings, and unterminated strings.
Plain/raw/byte single-line strings and ``#`` comments inside the literal are
scanned through correctly.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator

from pypeeker.intents import Intent, SymbolAnchor, TuplifyIntent
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
    AnchorTextMatches,
    AssignmentBindsList,
    InferredListBinding,
    Precondition,
    ScannableLiteral,
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

_OPEN_BRACKETS = (b"(", b"[", b"{")
_CLOSE_BRACKETS = (b")", b"]", b"}")


class TuplifyError(Exception):
    """Raised when a tuplify plan cannot be created.

    ``code`` is the stable refusal slug the superseded fix protocol's
    ``PreferTupleFix`` used for the same refusal, which ``check --fix``
    still reports verbatim. It is derived, never hardcoded at the raise
    site, from the failing
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


def _expect_assignment_list(content: bytes, after_name: int) -> int | None:
    """Offset of the ``[`` opening ``name = [...]``, or None when absent.

    Starting just past the variable name token, skips spaces/tabs, requires a
    single ``=`` (not ``==``), skips spaces/tabs again, and requires ``[``.
    Anything else — an annotation, an augmented assignment, a non-literal
    RHS — is not the shape the tuplify rule detected, so no offset.
    """
    i = after_name
    n = len(content)
    while i < n and content[i : i + 1] in (b" ", b"\t"):
        i += 1
    if i >= n or content[i : i + 1] != b"=" or content[i + 1 : i + 2] == b"=":
        return None
    i += 1
    while i < n and content[i : i + 1] in (b" ", b"\t"):
        i += 1
    if i >= n or content[i : i + 1] != b"[":
        return None
    return i


def _match_list_literal(content: bytes, open_off: int) -> tuple[int, int, bool, bool] | str:
    """Match the ``]`` closing the ``[`` at ``open_off`` by scanning bytes.

    Returns ``(close_offset, top_level_commas, has_elements, is_comprehension)``
    on success, or a human-readable reason string when the scan cannot proceed
    safely (the caller declines ``"ambiguous"``). ``is_comprehension`` is true
    when a top-level ``for`` keyword makes this a list comprehension
    (``[x for x in xs]``) rather than an element list — the two tuplify
    differently, so the caller must distinguish them.
    """
    depth = 0
    commas = 0
    has_elements = False
    is_comprehension = False
    i = open_off
    n = len(content)
    while i < n:
        b = content[i : i + 1]
        if b in _OPEN_BRACKETS:
            depth += 1
            if i > open_off:
                has_elements = True  # a nested literal/call/subscript is content
            i += 1
            continue
        if b in _CLOSE_BRACKETS:
            depth -= 1
            if depth == 0:
                if b != b"]":
                    return "unbalanced brackets scanning the list literal"
                return i, commas, has_elements, is_comprehension
            has_elements = True
            i += 1
            continue
        if b == b"#":
            newline = content.find(b"\n", i)
            i = n if newline == -1 else newline
            continue
        if b in (b"'", b'"'):
            if _string_prefix_has_f(content, i):
                return "the literal contains an f-string"
            if content[i : i + 3] in (b"'''", b'"""'):
                return "the literal contains a triple-quoted string"
            close = _scan_string(content, i)
            if close is None:
                return "the literal contains an unterminated string"
            has_elements = True
            i = close + 1
            continue
        if b == b",":
            if depth == 1:
                commas += 1
            has_elements = True
            i += 1
            continue
        if depth == 1 and content[i : i + 3] == b"for" and _is_standalone_word(content, i, 3):
            is_comprehension = True
            has_elements = True
            i += 3
            continue
        if b not in (b" ", b"\t", b"\r", b"\n"):
            has_elements = True
        i += 1
    return "no matching ']' found for the list literal"


def _is_standalone_word(content: bytes, start: int, length: int) -> bool:
    """True when the ``length``-byte token at ``start`` is not part of a longer word."""
    before = content[start - 1 : start] if start > 0 else b""
    after = content[start + length : start + length + 1]
    return not (before.isalnum() or before == b"_") and not (
        after.isalnum() or after == b"_"
    )


def _scan_string(content: bytes, quote_off: int) -> int | None:
    """Offset of the quote closing the single-line string at ``quote_off``."""
    quote = content[quote_off : quote_off + 1]
    i = quote_off + 1
    n = len(content)
    while i < n:
        b = content[i : i + 1]
        if b == b"\\":
            i += 2
            continue
        if b == quote:
            return i
        if b == b"\n":
            return None
        i += 1
    return None


def _string_prefix_has_f(content: bytes, quote_off: int) -> bool:
    """True when the string starting at ``quote_off`` carries an f/F prefix."""
    i = quote_off - 1
    prefix = b""
    while i >= 0 and content[i : i + 1].isalpha() and len(prefix) < 2:
        prefix = content[i : i + 1] + prefix
        i -= 1
    return b"f" in prefix.lower()


@dataclass
class _TuplifyState:
    """Values computed while evaluating preconditions, reused to build the edit."""

    file_path: str = ""
    content: bytes = b""
    symbol: Symbol | None = None
    open_off: int = 0
    close_off: int = 0
    top_level_commas: int = 0
    has_elements: bool = False
    is_comprehension: bool = False


class TuplifyPlanner:
    """Rewrite the never-mutated list literal bound to a variable as a tuple."""

    def __init__(
        self, index_store: IndexStore, transaction_store: TransactionStore
    ) -> None:
        self._index_store = index_store
        self._transaction_store = transaction_store
        self._engine = SemanticQueryEngine(index_store)

    def plan(self, anchor: SymbolAnchor, name: str) -> TransactionSummary:
        """Re-locate the literal via the current index and rewrite its brackets."""
        symbol_id = anchor.symbol_id
        state = _TuplifyState()
        evaluated, failure = evaluate_in_order(
            self._iter_preconditions(state, symbol_id, name)
        )
        if failure is not None:
            failing = evaluated[-1]
            raise TuplifyError(failing.slug, failure.reason, precondition=failing.name)

        if state.is_comprehension:
            # A comprehension has no tuple-literal form: ``[c for c in xs]``
            # becomes ``tuple(c for c in xs)``, NOT ``(c for c in xs,)`` — the
            # latter is a 1-tuple wrapping a generator (or a SyntaxError).
            open_new, close_new = "tuple(", ")"
        else:
            open_new = "("
            # ``[x]`` must become ``(x,)`` — ``(x)`` is just x — while ``[]``
            # and any literal with a top-level comma keep a plain ``)``.
            close_new = (
                ",)" if state.has_elements and state.top_level_commas == 0 else ")"
            )

        file_hash = IndexStore.compute_file_hash(
            self._index_store.project_root / state.file_path
        )
        edits = [
            EditEntry(
                op=EditOp.REPLACE,
                file=state.file_path,
                start=state.open_off,
                end=state.open_off + 1,
                old="[",
                new=open_new,
                file_hash=file_hash,
            ),
            EditEntry(
                op=EditOp.REPLACE,
                file=state.file_path,
                start=state.close_off,
                end=state.close_off + 1,
                old="]",
                new=close_new,
                file_hash=file_hash,
            ),
        ]
        tx_id = uuid.uuid4().hex[:12]
        header = TransactionHeader(
            tx_id=tx_id,
            symbol_id=symbol_id,
            old_name=name,
            new_name=name,
            created_at=datetime.now(timezone.utc).isoformat(),
            operation="tuplify",
        )
        self._transaction_store.save(header, edits, None)
        return TransactionSummary(
            tx_id=tx_id,
            operation="tuplify",
            symbol_id=symbol_id,
            old_name=name,
            new_name=name,
            files_affected=[state.file_path],
            edit_count=len(edits),
            created_at=header.created_at,
        )

    def _iter_preconditions(
        self, state: _TuplifyState, symbol_id: str, name: str
    ) -> Iterator[Precondition]:
        """Yield this tuplification's preconditions in evaluation order.

        The consumer must evaluate each yielded precondition before advancing
        (see :func:`~pypeeker.refactor.preconditions.evaluate_in_order`); the
        resolved file path, current bytes, symbol and the scanned literal's
        bracket offsets/shape are stashed on ``state`` for :meth:`plan`.
        """
        matches = [
            s
            for s in self._engine.find_symbol(symbol_id)
            if s.kind is SymbolKind.VARIABLE
        ]
        yield SymbolMatchUnambiguous(symbol_id, matches, noun="variable")
        found = SymbolMatchFound(symbol_id, matches, noun="variable")
        yield found
        state.file_path = found.symbol.location.file_path

        yield AnchorFileExists(self._index_store, state.file_path)
        index_fresh = AnchorIndexFresh(self._index_store, state.file_path)
        yield index_fresh
        state.content = index_fresh.content

        fresh_matches = [
            s
            for s in index_fresh.index.symbols
            if s.symbol_id == symbol_id and s.kind is SymbolKind.VARIABLE
        ]
        still = SymbolMatchFound(symbol_id, fresh_matches, noun="variable")
        yield still
        state.symbol = still.symbol

        yield InferredListBinding(name, state.symbol)

        name_bytes = name.encode("utf-8")
        offset = position_to_byte_offset(
            state.content,
            state.symbol.location.span.start.line,
            state.symbol.location.span.start.column,
        )
        yield AnchorTextMatches(state.content, offset, name)

        open_off = _expect_assignment_list(state.content, offset + len(name_bytes))
        yield AssignmentBindsList(name, open_off)
        state.open_off = open_off

        scan = _match_list_literal(state.content, open_off)
        scannable = ScannableLiteral(scan)
        yield scannable
        state.close_off = scannable.close_offset
        state.top_level_commas = scannable.top_level_commas
        state.has_elements = scannable.has_elements
        state.is_comprehension = scannable.is_comprehension


@register_planner(TuplifyIntent.kind)
def _materialize_tuplify(
    intent: Intent, store: IndexStore, tx_store: TransactionStore
) -> Materialized | str:
    """Re-plan a :class:`TuplifyIntent` against ``store`` (batch materializer)."""
    assert isinstance(intent, TuplifyIntent)
    try:
        summary = TuplifyPlanner(store, tx_store).plan(intent.anchor, intent.name)
    except TuplifyError as error:
        return MaterializeError(
            str(error), code=error.code, precondition=error.precondition
        )
    materialized = load_transaction(tx_store, summary.tx_id)
    materialized.summary = summary
    return materialized
