"""The shared ``symbol_id -> Symbol`` lookup every per-file probe reads through.

One helper, two named duplicate-id policies. :mod:`pypeeker.analysis.type_annotation`,
:mod:`pypeeker.analysis.calls` and :mod:`pypeeker.analysis.writes` each need
to turn a reference's recorded symbol id back into the :class:`Symbol` it
names; they used to build that map three separate ways. Symbol ids carry
``$N`` shadowing suffixes and are expected unique per file, but nothing in
the model enforces that (two same-named ``def`` bodies bind their locals to
one id), so on a collision the election has to be explicit:

* ``type_annotation`` elects the **first** binding (the ``next(...)`` scan
  the memo originally replaced).
* ``calls`` and ``writes`` elect the **last** binding (``last_wins=True``),
  because the frozen ``check`` rules and the DSL row builder
  (``dsl/universes.py``) classify receivers through a plain
  ``{s.symbol_id: s}`` comprehension, which is last-wins; the receiver
  probes must agree with them or the two engines report different purity
  findings on colliding ids. Unifying the two policies is a
  ``dsl-rewrite.md`` ledger item, not a refactor.

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


def symbols_by_id(file_index: FileIndex, *, last_wins: bool = False) -> dict[str, Symbol]:
    """Return ``symbol_id -> Symbol`` for ``file_index``, memoized on the instance.

    **First binding wins on a duplicate id** by default; ``last_wins=True``
    elects the last binding instead, the policy the receiver-classifying
    probes share with the frozen rules (see the module docstring). Each
    policy keeps its own memo.

    The guard rebuilds when ``symbols`` is rebound, resized, or when
    ``file_hash`` moves. Nothing in ``src`` mutates a built index's symbol
    list in place — the binder mutates ``BinderState`` before the
    ``FileIndex`` exists, and ``refactor.simulate.rebind_source`` builds a
    fresh one that ``OverlayIndexStore.save`` swaps in — so the guard is
    insurance against a future in-place mutation, not a live hazard.
    """
    symbols = file_index.symbols
    stamp = (len(symbols), file_index.file_hash)
    attr = _SYMBOL_INDEX_ATTR + ("_last" if last_wins else "")
    cached = getattr(file_index, attr, None)
    # The identity check holds a reference to the very list the map was built
    # from, so a rebound ``symbols`` can never be mistaken for the cached one.
    if cached is not None and cached[0] is symbols and cached[1] == stamp:
        return cached[2]
    table: dict[str, Symbol] = {}
    for symbol in symbols:
        if last_wins:
            table[symbol.symbol_id] = symbol
        else:
            table.setdefault(symbol.symbol_id, symbol)
    setattr(file_index, attr, (symbols, stamp, table))
    return table
