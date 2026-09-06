"""Star-import attribution: which names does ``from m import *`` actually supply?

The single home of the derivation shared across the ``check`` / ``refactor``
divide, in the same spirit as :mod:`pypeeker.analysis.docstrings`: the rule
that *reports* an unresolvable star and the planner that *rewrites* it must
agree on the supplied name list to the letter, or a repair rewrites a star to a
different set of names than the finding it repairs described. Neither of those
packages may import the other, so the derivation lives here and both consume
it.

The attribution model is first-star-wins: walk a file's stars in file order and
give each remaining unresolved bare name to the first star whose *indexed*
target publicly defines it. Names nobody can supply are the residue
:func:`attribute_names` returns alongside the attribution — a non-empty residue
means the file's star coverage is incomplete, which is what makes an unresolved
name ambiguous between the stars rather than attributable to one of them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pypeeker.models import (
    FileIndex,
    Symbol,
    SymbolKind,
    is_unresolved_attr,
    module_symbol_id,
)


def star_symbols(index: FileIndex) -> list[Symbol]:
    """The file's ``"*"`` IMPORT symbols, in file order."""
    stars = [s for s in index.symbols if s.kind is SymbolKind.IMPORT and s.name == "*"]
    stars.sort(key=lambda s: (s.location.span.start.line, s.location.span.start.column))
    return stars


def module_indexes(indexes: Sequence[FileIndex]) -> dict[str, FileIndex]:
    """Map each index's dotted module path to its :class:`FileIndex`.

    A plain dict assignment, so **the last index wins** when two indexed files
    collapse onto one module id (``proj/dup.py`` and ``proj/dup/__init__.py``
    both answer ``proj.dup``). That is deliberate and load-bearing: a caller
    that instead elected the *first* such file would resolve a star's target to
    a different module's public surface on exactly the shapes
    ``tests/fixtures/parity/boundaries`` and ``.../cycles`` exist to produce.
    """
    out: dict[str, FileIndex] = {}
    for index in indexes:
        module_id = module_symbol_id(index)
        if module_id is not None:
            out[module_id] = index
    return out


def public_surface(index: FileIndex) -> frozenset[str]:
    """Public module-level names of ``index`` — what ``import *`` can supply.

    Two documented approximations: ``__all__``'s contents are unavailable (the
    index records only that the name is bound), so every non-underscore
    module-level symbol counts, and imports count too because star semantics
    re-export them.
    """
    module_id = module_symbol_id(index)
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


def unresolved_bare_names(index: FileIndex) -> set[str]:
    """Bare unresolved reference names in ``index`` — star-supply candidates.

    A name a star supplies binds to nothing the binder can see, so it surfaces
    as an unresolved reference whose id is the bare name. ``<unresolved>.attr``
    sentinels and underscore-prefixed names are excluded — a star never
    supplies the latter absent ``__all__``, which this derivation ignores.
    """
    return {
        ref.symbol_id
        for ref in index.references
        if not ref.resolved
        and not is_unresolved_attr(ref.symbol_id)
        and ref.symbol_id.isidentifier()
        and not ref.symbol_id.startswith("_")
    }


def attribute_names(
    stars: Sequence[Symbol],
    unresolved: set[str],
    modules: Mapping[str, FileIndex],
) -> tuple[dict[str, list[str]], list[str]]:
    """Attribute unresolved names to star imports, first-star-wins.

    Returns ``(used_by, unattributed)``. Walks ``stars`` in file order; each
    remaining name goes to the first star whose *indexed* target publicly
    defines it, and a star with an unindexed target gets **no entry at all**
    rather than an empty one — which is why a consumer's unindexed branch is a
    partition on "is the target indexed" and not on ``name_count == 0``.
    ``unattributed`` is the sorted residue no star could supply; a reporting
    consumer with no repair to plan may discard it, a repairing consumer must
    refuse on it.

    ``used_by`` is keyed on ``symbol_id``, so two stars sharing one id would
    have the second overwrite the first — deliberate, because consumers then
    read ``used_by.get(star.symbol_id, [])`` per star and report the same
    overwritten list twice rather than inventing an attribution.
    """
    remaining = set(unresolved)
    used_by: dict[str, list[str]] = {}
    for star in stars:
        target = modules.get(star.imported_from)
        if target is None:
            continue
        supplied = sorted(remaining & public_surface(target))
        used_by[star.symbol_id] = supplied
        remaining.difference_update(supplied)
    return used_by, sorted(remaining)
