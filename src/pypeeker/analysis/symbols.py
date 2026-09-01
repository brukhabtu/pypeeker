"""The shared ``symbol_id -> Symbol`` lookup every per-file probe reads through.

One helper, one duplicate-id policy. :mod:`pypeeker.analysis.type_annotation`,
:mod:`pypeeker.analysis.calls` and :mod:`pypeeker.analysis.writes` each need
to turn a reference's recorded symbol id back into the :class:`Symbol` it
names; they used to build that map three separate ways, and two of them (a
plain dict comprehension) silently elected the *last* binding on a duplicate
id where the third elected the *first*. Symbol ids carry ``$N`` shadowing
suffixes and are expected unique per file, but nothing in the model enforces
that, so the policy has to be a single deliberate choice: **first binding
wins**, matching the ``next(...)`` scan the memo originally replaced.

The map is memoized on the :class:`~pypeeker.models.FileIndex` instance
rather than in an external table because a ``FileIndex`` is unhashable (a
mutable dataclass), so nothing can key on it; and because
``IndexStore.load`` hands out the same cached object repeatedly, so an
instance memo survives across rule invocations. It deliberately does *not*
live on ``FileIndex`` itself: ``models`` is a pure serialization leaf that
every other package imports, and lookup logic belongs in the layer that
needs it. Being a plain attribute rather than a dataclass field, it is
invisible to ``models.serialize.to_dict`` (which iterates
``dataclasses.fields``) and to the generated ``__eq__``.
"""

from __future__ import annotations

from pypeeker.models import FileIndex, Symbol

_SYMBOL_INDEX_ATTR = "_pypeeker_symbols_by_id"


def symbols_by_id(file_index: FileIndex) -> dict[str, Symbol]:
    """Return ``symbol_id -> Symbol`` for ``file_index``, memoized on the instance.

    **First binding wins on a duplicate id.** A dict comprehension would take
    the last match, which could change which annotation a trait reports or
    which receiver kind a write is classified with; ``setdefault`` keeps the
    lookup stable under collisions.

    The guard rebuilds when ``symbols`` is rebound, resized, or when
    ``file_hash`` moves. Nothing in ``src`` mutates a built index's symbol
    list in place — the binder mutates ``BinderState`` before the
    ``FileIndex`` exists, and ``refactor.simulate.rebind_source`` builds a
    fresh one that ``OverlayIndexStore.save`` swaps in — so the guard is
    insurance against a future in-place mutation, not a live hazard.
    """
    symbols = file_index.symbols
    stamp = (len(symbols), file_index.file_hash)
    cached = getattr(file_index, _SYMBOL_INDEX_ATTR, None)
    # The identity check holds a reference to the very list the map was built
    # from, so a rebound ``symbols`` can never be mistaken for the cached one.
    if cached is not None and cached[0] is symbols and cached[1] == stamp:
        return cached[2]
    table: dict[str, Symbol] = {}
    for symbol in symbols:
        table.setdefault(symbol.symbol_id, symbol)
    setattr(file_index, _SYMBOL_INDEX_ATTR, (symbols, stamp, table))
    return table
