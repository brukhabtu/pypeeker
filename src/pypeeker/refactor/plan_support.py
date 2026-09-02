"""Shared plumbing every planner module used to copy by hand.

Three pieces, each extracted from a preamble or epilogue that the planner
modules had replicated verbatim:

* :func:`iter_anchored_symbol` — the resolve-to-fresh-to-re-find precondition
  preamble of the symbol-anchored planners (delete-symbol, tuplify,
  rename-docstring-param, remove-import, rewrite-star-import): filter
  ``find_symbol`` by kind, demand an unambiguous hit, verify the owning file
  exists and its index is fresh, then re-find the symbol in the *fresh*
  index. It composes two halves: the project-wide resolve and
  :func:`iter_fresh_symbol` (the freshness pair plus the re-find), which is
  also usable on its own.
  Move-symbol resolves through
  :class:`~pypeeker.refactor.preconditions.SymbolResolvesUniquely` (a
  different refusal, and no kind filter at resolve time) and interleaves
  target-qualifying checks before the freshness half, so it uses only
  :func:`iter_fresh_symbol`.
* :func:`persist` — transaction persistence plus the
  :class:`~pypeeker.models.TransactionSummary` every ``plan()`` returns,
  deriving ``files_affected`` and ``edit_count`` by one rule.
* :func:`simple_materializer` — builds *and registers* the materializer
  for a planner with one ``plan()`` entry point: it re-plans an intent,
  turns the planner's :class:`PlanRefused` into a
  :class:`~pypeeker.refactor.registry.MaterializeError`, and loads the
  persisted transaction back as a
  :class:`~pypeeker.refactor.registry.Materialized`.

Plus :func:`method_override_conflicts`, the hierarchy question the rename
planner's ``method-override-safe`` precondition and the privatize
pre-filter both ask (each words its own refusal).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator, Protocol

from pypeeker.analysis import Hierarchy
from pypeeker.intents import Intent
from pypeeker.models import (
    EditEntry,
    FileCreateEntry,
    FileIndex,
    FileRenameEntry,
    Symbol,
    SymbolKind,
    TransactionHeader,
    TransactionSummary,
)
from pypeeker.query import SemanticQueryEngine
from pypeeker.refactor.preconditions import (
    AnchorFileExists,
    AnchorIndexFresh,
    Precondition,
    SymbolMatchFound,
    SymbolMatchUnambiguous,
)
from pypeeker.refactor.registry import (
    Materialized,
    MaterializeError,
    Materializer,
    load_transaction,
    register_planner,
)
from pypeeker.storage import IndexStore, TransactionStore


class PlanRefused(Exception):
    """Base of every planner's refusal: a message plus optional metadata.

    ``code`` is the stable machine-readable refusal slug, for the planners
    whose refusals map onto a legacy ``check --fix`` report code; ``None``
    for the rest. ``precondition`` (TASK-125, additive) names the failing
    :class:`~pypeeker.refactor.preconditions.Precondition` when the refusal
    came from a guarded precondition set; ``None`` otherwise. Each planner's
    concrete subclass keeps its own constructor and message wording; sharing
    the base is what lets :func:`simple_materializer` read both fields off
    any refusal uniformly.
    """

    code: str | None
    precondition: str | None

    def __init__(
        self, message: str, *, code: str | None = None, precondition: str | None = None
    ) -> None:
        """Store the message alongside the refusal ``code`` and ``precondition`` name."""
        super().__init__(message)
        self.code = code
        self.precondition = precondition


@dataclass
class AnchoredSymbol:
    """What :func:`iter_anchored_symbol` stashes for the planner that drove it.

    ``file_path`` is the owning file resolved from the project-wide match;
    ``content``/``index`` are that file's current bytes and hash-verified
    index; ``symbol`` is the target re-found in that fresh index. A planner's
    own precondition state subclasses this and adds what its later checks
    stash, so the helper writes straight into the planner's state.
    """

    file_path: str = ""
    content: bytes = b""
    index: FileIndex | None = None
    symbol: Symbol | None = None


class _FreshSymbolState(Protocol):
    """What :func:`iter_fresh_symbol` stashes: the fresh half of :class:`AnchoredSymbol`.

    A planner that resolves its target its own way (move-symbol) satisfies
    this structurally with its own state rather than subclassing
    :class:`AnchoredSymbol`.
    """

    content: bytes
    index: FileIndex | None
    symbol: Symbol | None


def _kind_matcher(
    kinds: tuple[SymbolKind, ...], matches: Callable[[Symbol], bool] | None
) -> Callable[[Symbol], bool]:
    """Build the ``kinds``-and-``matches`` predicate both halves filter with."""

    def is_match(symbol: Symbol) -> bool:
        return symbol.kind in kinds and (matches is None or matches(symbol))

    return is_match


def _iter_resolved_symbol(
    engine: SemanticQueryEngine,
    symbol_id: str,
    kinds: tuple[SymbolKind, ...],
    noun: str,
    state: AnchoredSymbol,
    *,
    resolves_to: str = "symbol",
    unambiguous_noun: str | None = None,
    matches: Callable[[Symbol], bool] | None = None,
) -> Iterator[Precondition]:
    """Yield the project-wide resolve preconditions for ``symbol_id``.

    Filters ``find_symbol``'s hits by ``kinds`` (and ``matches``), demands
    exactly one, and stashes its owning file on ``state.file_path``. ``noun``
    words the :class:`~pypeeker.refactor.preconditions.SymbolMatchFound`
    refusal; ``unambiguous_noun``/``resolves_to`` word the
    :class:`~pypeeker.refactor.preconditions.SymbolMatchUnambiguous` one
    (``unambiguous_noun`` defaults to ``noun``).
    """
    is_match = _kind_matcher(kinds, matches)
    candidates = [s for s in engine.find_symbol(symbol_id) if is_match(s)]
    yield SymbolMatchUnambiguous(
        symbol_id,
        candidates,
        noun=unambiguous_noun if unambiguous_noun is not None else noun,
        resolves_to=resolves_to,
    )
    found = SymbolMatchFound(symbol_id, candidates, noun=noun)
    yield found
    state.file_path = found.symbol.location.file_path


def iter_fresh_symbol(
    store: IndexStore,
    file_path: str,
    symbol_id: str,
    kinds: tuple[SymbolKind, ...],
    noun: str,
    state: _FreshSymbolState,
    *,
    matches: Callable[[Symbol], bool] | None = None,
) -> Iterator[Precondition]:
    """Yield the freshness-then-re-find preconditions for ``symbol_id`` in ``file_path``.

    Verifies ``file_path`` exists and its index is fresh, then re-finds
    ``symbol_id`` (filtered by ``kinds`` and ``matches``) in that *fresh*
    index; the file's current bytes, its index and the re-found symbol are
    stashed on ``state``. ``noun`` words the
    :class:`~pypeeker.refactor.preconditions.SymbolMatchFound` refusal.
    """
    is_match = _kind_matcher(kinds, matches)
    yield AnchorFileExists(store, file_path)
    index_fresh = AnchorIndexFresh(store, file_path)
    yield index_fresh
    state.content = index_fresh.content
    state.index = index_fresh.index

    fresh_matches = [
        s for s in index_fresh.index.symbols if s.symbol_id == symbol_id and is_match(s)
    ]
    still = SymbolMatchFound(symbol_id, fresh_matches, noun=noun)
    yield still
    state.symbol = still.symbol


def iter_anchored_symbol(
    engine: SemanticQueryEngine,
    store: IndexStore,
    symbol_id: str,
    kinds: tuple[SymbolKind, ...],
    noun: str,
    state: AnchoredSymbol,
    *,
    resolves_to: str = "symbol",
    unambiguous_noun: str | None = None,
    matches: Callable[[Symbol], bool] | None = None,
) -> Iterator[Precondition]:
    """Yield the resolve-to-fresh-to-re-find preconditions for ``symbol_id``.

    :func:`_iter_resolved_symbol` followed by :func:`iter_fresh_symbol` on the
    file it resolved. The consumer must evaluate each yielded precondition
    before advancing (see
    :func:`~pypeeker.refactor.preconditions.evaluate_in_order`); the resolved
    file path, its current bytes and fresh index, and the re-found symbol are
    stashed on ``state`` as each becomes known.

    ``kinds`` (and the optional extra ``matches`` predicate) filter both the
    project-wide ``find_symbol`` hits and the fresh index's symbols, so the
    same shape is demanded before and after the freshness check.
    """
    yield from _iter_resolved_symbol(
        engine,
        symbol_id,
        kinds,
        noun,
        state,
        resolves_to=resolves_to,
        unambiguous_noun=unambiguous_noun,
        matches=matches,
    )
    yield from iter_fresh_symbol(
        store, state.file_path, symbol_id, kinds, noun, state, matches=matches
    )


def persist(
    tx_store: TransactionStore,
    operation: str,
    symbol_id: str,
    old_name: str,
    new_name: str,
    edits: list[EditEntry],
    *,
    file_rename: FileRenameEntry | None = None,
    creates: Sequence[FileCreateEntry] = (),
    files_affected: Sequence[str] | None = None,
    include_file: bool = False,
    include_exports: bool = False,
) -> TransactionSummary:
    """Persist a freshly planned transaction and describe it as a summary.

    Mints the transaction id and ``created_at``, writes the header (with the
    rename-only ``include_file``/``include_exports`` flags) plus ``edits``,
    ``file_rename`` and ``creates`` through ``tx_store``, and returns the
    :class:`~pypeeker.models.TransactionSummary` the planner hands back.

    The counting rule: ``edit_count`` is every persisted entry — one per
    text edit, one per file creation, one for a file rename — so it matches
    what ``transactions show`` lists. ``files_affected`` defaults to the
    sorted set of files those edits and creations touch; a planner whose
    notion of "affected" is wider than its edits (rename counts files whose
    candidate locations failed the text guard, and the renamed file's new
    path) passes it explicitly.
    """
    tx_id = uuid.uuid4().hex[:12]
    header = TransactionHeader(
        tx_id=tx_id,
        symbol_id=symbol_id,
        old_name=old_name,
        new_name=new_name,
        created_at=datetime.now(timezone.utc).isoformat(),
        operation=operation,
        include_file=include_file,
        include_exports=include_exports,
    )
    tx_store.save(header, edits, file_rename, creates=list(creates))
    if files_affected is None:
        touched = {edit.file for edit in edits} | {create.path for create in creates}
        files_affected = sorted(touched)
    return TransactionSummary(
        tx_id=tx_id,
        operation=operation,
        symbol_id=symbol_id,
        old_name=old_name,
        new_name=new_name,
        files_affected=list(files_affected),
        edit_count=len(edits) + len(creates) + (1 if file_rename else 0),
        created_at=header.created_at,
    )


def simple_materializer(
    intent_cls: type[Intent],
    planner_cls: Callable[[IndexStore, TransactionStore], object],
    args_fn: Callable[[Intent], tuple[object, ...]],
    kwargs_fn: Callable[[Intent], Mapping[str, object]] | None = None,
) -> Materializer:
    """Build and register the materializer for a planner with one ``plan()``.

    Registers the result under ``intent_cls.kind`` via
    :func:`~pypeeker.refactor.registry.register_planner` — calling this at
    module level is the registration, so the planner module needs no name
    for it. The returned callable has the exact ``(intent, store, tx_store)``
    contract :mod:`pypeeker.refactor.registry` describes: it asserts the
    intent is an ``intent_cls``, constructs ``planner_cls(store, tx_store)``
    and calls its ``plan(*args_fn(intent), **kwargs_fn(intent))``. A refusal
    (any :class:`PlanRefused`) becomes a
    :class:`~pypeeker.refactor.registry.MaterializeError` carrying the
    error's ``precondition`` and — for the planners whose error has one —
    its stable refusal ``code``; any other exception propagates.

    On success the persisted transaction is loaded back through
    :func:`~pypeeker.refactor.registry.load_transaction` and the planner's
    own summary is stashed on it (TASK-123): the simulation loop does not
    use that field, but since TASK-129 :func:`~pypeeker.refactor.batch.run_batch`
    carries it out on ``ExecutedIntent.summary``, which is how a
    single-intent submit (:mod:`pypeeker.app.submit`, now a batch of one)
    echoes output byte-identical to a direct ``plan()`` call.
    """

    def materialize(
        intent: Intent, store: IndexStore, tx_store: TransactionStore
    ) -> Materialized | str:
        """Re-plan ``intent`` against ``store`` (batch materializer)."""
        assert isinstance(intent, intent_cls)
        kwargs = dict(kwargs_fn(intent)) if kwargs_fn is not None else {}
        try:
            summary = planner_cls(store, tx_store).plan(*args_fn(intent), **kwargs)
        except PlanRefused as error:
            return MaterializeError(
                str(error), code=error.code, precondition=error.precondition
            )
        materialized = load_transaction(tx_store, summary.tx_id)
        materialized.summary = summary
        return materialized

    return register_planner(intent_cls.kind)(materialize)


def method_override_conflicts(
    hierarchy: Hierarchy, symbol: Symbol
) -> tuple[list[str], list[str], str | None]:
    """What makes renaming method ``symbol`` unsafe for its override pairs.

    Returns ``(overrides, overridden_by, unknown_mro_owner)``: the base
    methods ``symbol`` overrides, the subclass methods overriding it (both as
    the hierarchy reports them, unsorted), and the owning class id when its
    base chain is incomplete (``mro_unknown``) — or ``None`` when the chain
    is fully known. All three empty/``None`` means the rename splits no
    override pair the index can see. Callers word their own refusal.
    """
    overrides = hierarchy.overrides(symbol.symbol_id)
    overridden_by = hierarchy.overridden_by(symbol.symbol_id)
    owner = symbol.parent_scope_id
    unknown = owner if owner is not None and hierarchy.mro_unknown(owner) else None
    return overrides, overridden_by, unknown


__all__ = [
    "AnchoredSymbol",
    "PlanRefused",
    "iter_anchored_symbol",
    "iter_fresh_symbol",
    "method_override_conflicts",
    "persist",
    "simple_materializer",
]
