"""Footprints and effects: the conflict/remap algebra for refactor intents.

The composite batch planner (TASK-88) schedules :mod:`pypeeker.refactor.intents`
by two declarations each intent makes about itself:

* a :class:`Footprint` — what the transform *reads* and *writes* (symbol-id
  prefixes, file paths, derived-fact keys). Two intents conflict when one's
  writes intersect the other's writes or reads; :meth:`Footprint.conflicts_with`
  is the pure function deciding that.
* an :class:`Effect` — what applying the transform *does* to the world's
  names (ids renamed/deleted/created, files written/renamed). Effects drive
  anchor remapping: after one intent executes, every pending intent is
  rewritten through the effect's substitution
  (:meth:`pypeeker.refactor.intents.Intent.remap`).

Symbol-id prefixes
==================

Footprint symbol sets and effect rename/delete entries are *prefixes* over
the id grammar of :mod:`pypeeker.models.symbol_id`
(``module.path:Scope.Chain:local$N``): a write to ``m:Foo`` affects
``m:Foo.method`` and ``m:Foo:attr`` but **not** ``m:Foobar`` (no separator
boundary) and **not** ``m:Foo$2`` (a ``$N`` suffix is a distinct shadow
binding, not a descendant). :func:`affects` is the single place that
containment rule lives.

Fact keys (granularity, honestly)
=================================

``reads_facts`` entries are opaque strings with a two-shape grammar:

* ``"name"`` (no ``:``) — a *global* fact such as ``"callgraph"`` or
  ``"hierarchy"``. Coarse on purpose: a global fact is invalidated by **any**
  write (symbols or files) of the other footprint, so an intent reading one
  conflicts with every writer.
* ``"name:<symbol-id>"`` — a fact scoped to a symbol, e.g. ``"purity:m:f"``.
  Invalidated only when the other footprint's ``writes_symbols`` overlaps the
  scope id prefix-aware (in either direction).

Deliberate limitation: ``writes_files`` does **not** invalidate
symbol-scoped facts (we cannot map a file path to the symbol ids it defines
without a store, and ``conflicts_with`` must stay pure). Intents that mutate
a file also declare the symbol prefixes they touch, which carries the
invalidation. Facts are read-only — there is no ``writes_facts`` — so fact
conflicts are always write/read.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum

from pypeeker.models.symbol_id import strip_shadow

_SEPARATORS = (".", ":")
"""Id-grammar separators that mark a descendant boundary after a prefix."""


def affects(prefix: str, symbol_id: str) -> bool:
    """True when a write to the id ``prefix`` touches ``symbol_id``.

    Containment over the :mod:`pypeeker.models.symbol_id` grammar:

    * exact match (``m:Foo`` affects ``m:Foo``);
    * descendant: ``symbol_id`` continues past ``prefix`` with a ``.`` or
      ``:`` separator (``m:Foo`` affects ``m:Foo.method`` and ``m:f`` affects
      ``m:f:x``; a bare module ``pkg.mod`` affects ``pkg.mod:X``).

    Mere string prefixing is *not* containment: ``m:Foo`` does not affect
    ``m:Foobar``, and a ``$N`` shadow suffix names a distinct binding, so
    ``m:Foo`` does not affect ``m:Foo$2``.
    """
    if prefix == symbol_id:
        return True
    if not symbol_id.startswith(prefix):
        return False
    return symbol_id[len(prefix)] in _SEPARATORS


def replace_leaf_name(symbol_id: str, new_name: str) -> str:
    """The id ``symbol_id`` would carry after renaming its leaf to ``new_name``.

    Replaces the final name segment (after the last ``.`` or ``:``),
    preserving any ``$N`` shadow suffix: ``m:Foo.method`` -> ``m:Foo.run``,
    ``m:f:x$2`` -> ``m:f:y$2``, and a bare module path ``pkg.mod`` ->
    ``pkg.new``. This is a *prediction* — after a real rename the binder may
    assign different shadow ordinals — which is exactly the fidelity anchor
    remapping needs.
    """
    base = strip_shadow(symbol_id)
    suffix = symbol_id[len(base):]
    cut = max(base.rfind(sep) for sep in _SEPARATORS)
    if cut == -1:
        return new_name + suffix
    return base[: cut + 1] + new_name + suffix


class ConflictKind(str, Enum):
    """Machine-readable kind of footprint intersection.

    * ``WRITE_WRITE`` — both intents write the same resource.
    * ``WRITE_READ``  — one intent writes what the other reads (either
      direction; :meth:`Footprint.conflicts_with` is symmetric).
    """

    WRITE_WRITE = "write-write"
    WRITE_READ = "write-read"


@dataclass(frozen=True)
class ConflictReport:
    """Why two footprints cannot commute.

    ``dimension`` names the resource axis (``"symbols"``, ``"files"`` or
    ``"facts"``) and ``items`` lists the overlapping ids / paths / fact keys,
    sorted for determinism. When footprints overlap on several axes, the
    report covers the first by a fixed precedence (symbols before files
    before facts, write/write before write/read) — one witness is enough for
    the scheduler to order or refuse the pair.
    """

    kind: ConflictKind
    dimension: str
    items: tuple[str, ...]


def _frozen(values: Iterable[str]) -> frozenset[str]:
    """Coerce any iterable of strings to a frozenset."""
    return values if isinstance(values, frozenset) else frozenset(values)


def _pairs(value: Mapping[str, str] | Iterable[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    """Coerce a mapping or iterable of pairs to a sorted tuple of pairs."""
    items = value.items() if isinstance(value, Mapping) else value
    return tuple(sorted(tuple(pair) for pair in items))


def _symbol_overlap(writes: frozenset[str], targets: frozenset[str]) -> set[str]:
    """Ids involved in a prefix-aware intersection of two symbol sets.

    Prefix-aware in both directions: a write to ``m:Foo`` overlaps a target
    ``m:Foo.method`` (the write subsumes the target) and a write to
    ``m:Foo.method`` overlaps a target ``m:Foo`` (the write lands inside it).
    Returns both sides of each overlapping pair.
    """
    overlap: set[str] = set()
    for w in writes:
        for t in targets:
            if affects(w, t) or affects(t, w):
                overlap.add(w)
                overlap.add(t)
    return overlap


def _fact_overlap(writer: "Footprint", reader: "Footprint") -> set[str]:
    """Fact keys of ``reader`` invalidated by ``writer``'s writes.

    Granularity per the module docstring: a global key (no ``:``) is
    invalidated by any write at all; a ``name:<symbol-id>`` key only by a
    symbol write overlapping the scope id prefix-aware (either direction).
    """
    invalidated: set[str] = set()
    writes_anything = bool(writer.writes_symbols or writer.writes_files)
    for key in reader.reads_facts:
        _, sep, scope = key.partition(":")
        if not sep:
            if writes_anything:
                invalidated.add(key)
        elif any(affects(w, scope) or affects(scope, w) for w in writer.writes_symbols):
            invalidated.add(key)
    return invalidated


@dataclass(frozen=True)
class Footprint:
    """What a transform reads and writes, declared up front.

    Symbol sets hold id *prefixes* (see :func:`affects`); file sets hold
    project-root-relative paths; ``reads_facts`` holds opaque fact keys (see
    the module docstring for the grammar and the chosen granularity).

    Frozen and hashable; constructor arguments may be any iterables and are
    normalised to frozensets.
    """

    reads_symbols: frozenset[str] = frozenset()
    writes_symbols: frozenset[str] = frozenset()
    reads_files: frozenset[str] = frozenset()
    writes_files: frozenset[str] = frozenset()
    reads_facts: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        """Normalise every field to a frozenset so any iterable is accepted."""
        for name in (
            "reads_symbols",
            "writes_symbols",
            "reads_files",
            "writes_files",
            "reads_facts",
        ):
            object.__setattr__(self, name, _frozen(getattr(self, name)))

    def conflicts_with(self, other: "Footprint") -> ConflictReport | None:
        """The first witness that this footprint and ``other`` cannot commute.

        Pure and symmetric: depends only on the two footprints (``a.conflicts_with(b)``
        reports iff ``b.conflicts_with(a)`` does, with the same kind/dimension).
        Checks, in fixed precedence order:

        1. write/write symbol overlap (prefix-aware both directions),
        2. write/read symbol overlap (either direction),
        3. write/write file overlap,
        4. write/read file overlap (either direction),
        5. fact invalidation (always write/read; see :func:`_fact_overlap`).

        Returns ``None`` when the footprints are disjoint on every axis.
        """
        checks: tuple[tuple[ConflictKind, str, set[str]], ...] = (
            (
                ConflictKind.WRITE_WRITE,
                "symbols",
                _symbol_overlap(self.writes_symbols, other.writes_symbols),
            ),
            (
                ConflictKind.WRITE_READ,
                "symbols",
                _symbol_overlap(self.writes_symbols, other.reads_symbols)
                | _symbol_overlap(other.writes_symbols, self.reads_symbols),
            ),
            (
                ConflictKind.WRITE_WRITE,
                "files",
                set(self.writes_files & other.writes_files),
            ),
            (
                ConflictKind.WRITE_READ,
                "files",
                set(self.writes_files & other.reads_files)
                | set(other.writes_files & self.reads_files),
            ),
            (
                ConflictKind.WRITE_READ,
                "facts",
                _fact_overlap(self, other) | _fact_overlap(other, self),
            ),
        )
        for kind, dimension, items in checks:
            if items:
                return ConflictReport(kind, dimension, tuple(sorted(items)))
        return None


@dataclass(frozen=True)
class Effect:
    """What applying a transform does to the world's names.

    * ``renamed`` — id substitution, old id -> new id. Applied prefix-aware:
      renaming ``m:Foo`` to ``m:Bar`` carries ``m:Foo.method`` to
      ``m:Bar.method`` (see :meth:`remap_id`).
    * ``deleted`` / ``created`` — symbol-id prefixes removed from / added to
      the world.
    * ``files_written`` — paths whose bytes changed.
    * ``files_renamed`` — path substitution, old path -> new path
      (exact-match; paths have no prefix grammar here).

    Frozen and hashable: mapping-like fields are normalised to sorted tuples
    of pairs, set-like fields to frozensets, so construction order never
    leaks into equality or hashing. ``renamed``/``files_renamed`` accept a
    ``dict`` or any iterable of pairs.
    """

    renamed: tuple[tuple[str, str], ...] = ()
    deleted: frozenset[str] = frozenset()
    created: frozenset[str] = frozenset()
    files_written: frozenset[str] = frozenset()
    files_renamed: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        """Normalise pair fields to sorted tuples and set fields to frozensets."""
        object.__setattr__(self, "renamed", _pairs(self.renamed))
        object.__setattr__(self, "files_renamed", _pairs(self.files_renamed))
        for name in ("deleted", "created", "files_written"):
            object.__setattr__(self, name, _frozen(getattr(self, name)))

    def remap_id(self, symbol_id: str) -> str | None:
        """``symbol_id`` after this effect, or ``None`` if the effect deleted it.

        Deletion wins (prefix-aware: deleting ``m:Foo`` takes ``m:Foo.method``
        with it). Otherwise an exact rename entry applies first; failing that,
        the longest renamed prefix descends into the id (``m:Foo -> m:Bar``
        maps ``m:Foo.method`` to ``m:Bar.method``). Ids the effect does not
        mention come back unchanged.
        """
        for d in self.deleted:
            if affects(d, symbol_id):
                return None
        for old, new in self.renamed:
            if old == symbol_id:
                return new
        for old, new in sorted(self.renamed, key=lambda p: -len(p[0])):
            if affects(old, symbol_id):
                return new + symbol_id[len(old):]
        return symbol_id

    def remap_file(self, path: str) -> str:
        """``path`` after this effect's file renames (exact-match, no deletion)."""
        for old, new in self.files_renamed:
            if old == path:
                return new
        return path

    def _preimage_id(self, symbol_id: str) -> str:
        """The pre-effect id that this effect maps to ``symbol_id``.

        Inverts ``renamed`` (exact entry first, then longest-prefix descent);
        ids no rename produced are their own preimage. Assumes a well-formed
        (injective) substitution, which every per-intent predicted effect is.
        """
        for old, new in self.renamed:
            if new == symbol_id:
                return old
        for old, new in sorted(self.renamed, key=lambda p: -len(p[1])):
            if affects(new, symbol_id):
                return old + symbol_id[len(new):]
        return symbol_id

    def _preimage_file(self, path: str) -> str:
        """The pre-effect path that this effect maps to ``path`` (exact-match)."""
        for old, new in self.files_renamed:
            if new == path:
                return old
        return path

    def then(self, other: "Effect") -> "Effect":
        """The single effect equivalent to applying ``self`` then ``other``.

        Substitutions compose (``A -> B`` then ``B -> C`` yields ``A -> C``,
        including prefix interactions: renaming ``m:Foo -> m:Bar`` then
        ``m:Bar.method -> m:Bar.run`` records ``m:Foo.method -> m:Bar.run``).
        A rename whose target ``other`` deletes becomes a deletion of the
        original id; an id ``self`` created and ``other`` deleted vanishes
        from both sets. Composition operates on the *declared* entries — the
        scheduler applies effects one at a time, so ``then`` exists for
        reporting and for collapsing an executed batch into one substitution.
        """
        renamed: dict[str, str] = {}
        deleted = set(self.deleted)

        for old, new in self.renamed:
            target = other.remap_id(new)
            if target is None:
                deleted.add(old)
            elif target != old:
                renamed[old] = target
        for old, new in other.renamed:
            pre = self._preimage_id(old)
            if pre in self.created:
                continue  # rename of an id self created: tracked via created below
            if any(affects(d, pre) for d in self.deleted):
                continue  # self already deleted it; other's rename is moot
            renamed.setdefault(pre, new)

        for d in other.deleted:
            pre = self._preimage_id(d)
            if pre not in self.created:
                deleted.add(pre)

        created: set[str] = set(other.created)
        for c in self.created:
            target = other.remap_id(c)
            if target is not None:
                created.add(target)

        files_renamed: dict[str, str] = {}
        for old, new in self.files_renamed:
            target = other.remap_file(new)
            if target != old:
                files_renamed[old] = target
        for old, new in other.files_renamed:
            files_renamed.setdefault(self._preimage_file(old), new)

        files_written = {other.remap_file(f) for f in self.files_written}
        files_written |= set(other.files_written)

        return Effect(
            renamed=renamed,
            deleted=deleted,
            created=created,
            files_written=files_written,
            files_renamed=files_renamed,
        )


# Re-exported convenience: an empty footprint/effect singletons would invite
# accidental sharing of mutable state if these classes were not frozen; they
# are, so module-level defaults are safe and read better at call sites.
EMPTY_FOOTPRINT = Footprint()
"""A footprint that reads and writes nothing (conflicts with nothing)."""

EMPTY_EFFECT = Effect()
"""An effect that changes nothing (identity for :meth:`Effect.then`)."""


__all__ = [
    "affects",
    "replace_leaf_name",
    "ConflictKind",
    "ConflictReport",
    "Footprint",
    "Effect",
    "EMPTY_FOOTPRINT",
    "EMPTY_EFFECT",
]
