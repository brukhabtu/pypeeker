"""The substrate a selection runs against: the indexes in scope, and the resolver over them.

A selection is a description; a :class:`Corpus` is the thing it is described
*about*. Everything the DSL can read comes from here, which is what makes
:attr:`pypeeker.dsl.Selection.reach` derivable at all: an expression that only
touches :attr:`Corpus.indexes` reaches ``FILE``, and an expression that reaches
for :attr:`Corpus.resolver` has left the file behind.

Shaped after :class:`pypeeker.check.context.CheckContext` — the same
store-plus-indexes-plus-lazy-resolver arrangement — but written out separately
rather than reused, because ``dsl`` may not import ``check``. Two deliberate
differences from that class:

* **No tree.** Nothing in the read half needs the package/module symbol tree,
  and importing ``treebuild`` to build one would put an entry in this
  package's layering allow-list that no real import exercises — which pypeeker's
  own ``import-boundaries`` rule reports as a finding.
* **Src roots are injected, not discovered.** The corpus is handed the roots it
  should consider rather than reading ``pyproject.toml`` itself, so ``project``
  stays out of the allow-list too. ``cli.py``, the composition root, is where
  configuration is read.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable
from typing import Any, TypeVar

from pypeeker.models import FileIndex, Symbol
from pypeeker.resolve import CrossModuleResolver
from pypeeker.storage import IndexStore

# Spelled as a TypeVar rather than with PEP 695's `def memo[T](...)`: the binder
# does not bind a method's inline type parameters, so the annotation reads as an
# unresolved reference and pypeeker's own no-unresolved-refs rule fires on it.
_T = TypeVar("_T")


class Corpus:
    """Every indexed file a selection may look at, plus the resolver over them.

    ``src_roots`` filters :meth:`IndexStore.list_indexed_files` by path prefix,
    using the same ``prefix.rstrip("/") + "/"`` rule the check engine applies,
    so a DSL selection and a check run see the same set of files for the same
    configuration. An empty ``src_roots`` means "every indexed file".

    Indexes and the resolver are both built lazily and cached for the corpus's
    lifetime: a selection that never follows a cross-file step never pays to
    construct a :class:`~pypeeker.resolve.CrossModuleResolver`.

    :meth:`memo` generalizes that caching to any derived table a project-reach
    expression needs — a projected id column, a usage-origins map, a primitive
    fact's sweep — so "materialized once per run" is a property of the
    substrate rather than a discipline each rule has to remember.
    """

    def __init__(self, store: IndexStore, src_roots: Iterable[str] = ()) -> None:
        self._store = store
        self._src_prefixes = tuple(root.rstrip("/") + "/" for root in src_roots)
        self._indexes: tuple[FileIndex, ...] | None = None
        self._resolver: CrossModuleResolver | None = None
        self._located: dict[str, tuple[Symbol, FileIndex]] | None = None
        self._memo: dict[Hashable, Any] = {}

    def memo(self, key: Hashable, build: Callable[[], _T]) -> _T:
        """Return ``build()``, computed once per ``key`` for this corpus's lifetime.

        The corpus is the natural lifetime for a project-wide sweep. A primitive
        fact such as the import-boundary verdict table is one pass over every
        index; ``import-boundaries`` reads that one pass from more than one of
        its parts, and recomputing it per part would spend twice the time to
        get the same answer — the corpus is immutable for the duration of a run,
        so "the same answer" is a guarantee rather than a hope.

        Deliberately **not** a general cache: nothing here evicts, and nothing
        invalidates — nothing in this package mutates a corpus, so a cached
        table can never outlive the indexes it was derived from. A key must
        identify the computation completely, and **structurally** — what the
        table is computed from (a fact's name *and* its parameters, a
        projected column's description), never a human label — so two
        independently-built descriptions of the same table share one
        computation, and two different tables that happen to share a name do
        not silently share a value.

        Args:
            key: a hashable identifying the computation, not the caller.
            build: computes the value on the first call for ``key``.

        Returns:
            The memoized value.
        """
        if key not in self._memo:
            self._memo[key] = build()
        return self._memo[key]

    @property
    def store(self) -> IndexStore:
        """The index store this corpus reads from."""
        return self._store

    @property
    def src_roots(self) -> tuple[str, ...]:
        """The configured source-root prefixes, normalized to end in ``/``."""
        return self._src_prefixes

    @property
    def indexes(self) -> tuple[FileIndex, ...]:
        """Every :class:`~pypeeker.models.FileIndex` under the configured roots (lazy).

        Ordered by indexed path, so a selection's row order is stable across
        runs — which matters because written order is normative here and a
        caller comparing two runs should not see rows shuffle.
        """
        if self._indexes is None:
            loaded: list[FileIndex] = []
            for source_path in self._store.list_indexed_files():
                if self._src_prefixes and not any(
                    source_path.startswith(prefix) for prefix in self._src_prefixes
                ):
                    continue
                file_index = self._store.load(source_path)
                if file_index is not None:
                    loaded.append(file_index)
            self._indexes = tuple(loaded)
        return self._indexes

    @property
    def resolver(self) -> CrossModuleResolver:
        """Cross-module resolver over :attr:`indexes` (lazy, shared).

        Touching this is what makes an expression's reach ``PROJECT``; the
        follow steps that consult it are declared ``PROJECT`` in the universe
        tables precisely so the derived reach and the actual substrate use
        cannot drift apart.
        """
        if self._resolver is None:
            self._resolver = CrossModuleResolver(list(self.indexes))
        return self._resolver

    def locate(self, symbol_id: str) -> tuple[Symbol, FileIndex] | None:
        """Find ``symbol_id`` in the corpus, returning it with its owning index.

        Returns ``None`` when nothing in the corpus declares that id — which is
        the normal outcome for an id that resolves outside the source roots
        (a stdlib or third-party import). Callers treat a miss as "no row",
        never as an error: loudness belongs to anchor *resolution*, not to
        navigation.

        A module id is not injective over indexed files (see
        storage-transaction-architecture.md -> Symbol IDs), so when two
        indexed files share one, ``symbol_id`` may be ambiguous too. This
        elects the *first* file in indexed-path order (``setdefault`` over
        :attr:`indexes`, itself ordered by :meth:`IndexStore.list_indexed_files`,
        which is sorted) — deliberately the opposite convention from
        :class:`~pypeeker.resolve.CrossModuleResolver`, which elects the
        *last* file for the same kind of collision. Unifying the two would
        move frozen-engine-observable output for a case with no more-correct
        answer, so both elections are documented rather than reconciled.
        """
        if self._located is None:
            table: dict[str, tuple[Symbol, FileIndex]] = {}
            for file_index in self.indexes:
                for symbol in file_index.symbols:
                    table.setdefault(symbol.symbol_id, (symbol, file_index))
            self._located = table
        return self._located.get(symbol_id)
