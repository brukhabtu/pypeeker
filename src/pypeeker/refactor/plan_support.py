"""Shared plumbing every planner module used to copy by hand.

Three pieces, each extracted from a preamble or epilogue that the planner
modules had replicated verbatim:

* :func:`iter_anchored_symbol` — the resolve-to-fresh-to-re-find precondition
  preamble of the symbol-anchored planners (delete-symbol, tuplify,
  rename-docstring-param, remove-import, rewrite-star-import): filter
  ``find_symbol`` by kind, demand an unambiguous hit, verify the owning file
  exists and its index is fresh, then re-find the symbol in the *fresh*
  index. Move-symbol keeps its own variant: it resolves through
  :class:`~pypeeker.refactor.preconditions.SymbolResolvesUniquely` (a
  different refusal, and no kind filter at resolve time) and interleaves
  target-qualifying checks between the resolve and the freshness pair.
* :func:`persist` — transaction persistence plus the
  :class:`~pypeeker.models.TransactionSummary` every ``plan()`` returns,
  deriving ``files_affected`` and ``edit_count`` by one rule.
* :func:`simple_materializer` — the ``@register_planner`` wrapper that
  re-plans an intent, turns the planner's refusal into a
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
from typing import Iterator

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
)
from pypeeker.storage import IndexStore, TransactionStore


@dataclass
class AnchoredSymbol:
    """What :func:`iter_anchored_symbol` stashes for the planner that drove it.

    ``file_path`` is the owning file resolved from the project-wide match;
    ``content``/``index`` are that file's current bytes and hash-verified
    index; ``symbol`` is the target re-found in that fresh index.
    """

    file_path: str = ""
    content: bytes = b""
    index: FileIndex | None = None
    symbol: Symbol | None = None


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

    The consumer must evaluate each yielded precondition before advancing
    (see :func:`~pypeeker.refactor.preconditions.evaluate_in_order`); the
    resolved file path, its current bytes and fresh index, and the re-found
    symbol are stashed on ``state`` as each becomes known.

    ``kinds`` (and the optional extra ``matches`` predicate) filter both the
    project-wide ``find_symbol`` hits and the fresh index's symbols, so the
    same shape is demanded before and after the freshness check. ``noun``
    words the :class:`~pypeeker.refactor.preconditions.SymbolMatchFound`
    refusals; ``unambiguous_noun``/``resolves_to`` word the
    :class:`~pypeeker.refactor.preconditions.SymbolMatchUnambiguous` one
    (``unambiguous_noun`` defaults to ``noun``).
    """

    def is_match(symbol: Symbol) -> bool:
        return symbol.kind in kinds and (matches is None or matches(symbol))

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

    yield AnchorFileExists(store, state.file_path)
    index_fresh = AnchorIndexFresh(store, state.file_path)
    yield index_fresh
    state.content = index_fresh.content
    state.index = index_fresh.index

    fresh_matches = [
        s for s in index_fresh.index.symbols if s.symbol_id == symbol_id and is_match(s)
    ]
    still = SymbolMatchFound(symbol_id, fresh_matches, noun=noun)
    yield still
    state.symbol = still.symbol


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
    error_cls: type[Exception],
    args_fn: Callable[[Intent], tuple[object, ...]],
    kwargs_fn: Callable[[Intent], Mapping[str, object]] | None = None,
) -> Materializer:
    """Build the materializer for a planner with one ``plan()`` entry point.

    The returned callable has the exact ``(intent, store, tx_store)``
    contract :mod:`pypeeker.refactor.registry` describes: it asserts the
    intent is an ``intent_cls``, constructs ``planner_cls(store, tx_store)``
    and calls its ``plan(*args_fn(intent), **kwargs_fn(intent))``. A refusal
    (``error_cls``) becomes a :class:`~pypeeker.refactor.registry.MaterializeError`
    carrying the error's ``precondition`` and — for the planners whose error
    has one — its stable refusal ``code``; any other exception propagates.

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
        except error_cls as error:
            return MaterializeError(
                str(error),
                code=getattr(error, "code", None),
                precondition=getattr(error, "precondition", None),
            )
        materialized = load_transaction(tx_store, summary.tx_id)
        materialized.summary = summary
        return materialized

    return materialize


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
    "iter_anchored_symbol",
    "method_override_conflicts",
    "persist",
    "simple_materializer",
]
