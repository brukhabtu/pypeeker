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

from collections.abc import Iterable

from pypeeker.models import FileIndex, Symbol
from pypeeker.resolve import CrossModuleResolver
from pypeeker.storage import IndexStore


class Corpus:
    """Every indexed file a selection may look at, plus the resolver over them.

    ``src_roots`` filters :meth:`IndexStore.list_indexed_files` by path prefix,
    using the same ``prefix.rstrip("/") + "/"`` rule the check engine applies,
    so a DSL selection and a check run see the same set of files for the same
    configuration. An empty ``src_roots`` means "every indexed file".

    Indexes and the resolver are both built lazily and cached for the corpus's
    lifetime: a selection that never follows a cross-file step never pays to
    construct a :class:`~pypeeker.resolve.CrossModuleResolver`.
    """

    def __init__(self, store: IndexStore, src_roots: Iterable[str] = ()) -> None:
        self._store = store
        self._src_prefixes = tuple(root.rstrip("/") + "/" for root in src_roots)
        self._indexes: tuple[FileIndex, ...] | None = None
        self._resolver: CrossModuleResolver | None = None
        self._located: dict[str, tuple[Symbol, FileIndex]] | None = None

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
        """
        if self._located is None:
            table: dict[str, tuple[Symbol, FileIndex]] = {}
            for file_index in self.indexes:
                for symbol in file_index.symbols:
                    table.setdefault(symbol.symbol_id, (symbol, file_index))
            self._located = table
        return self._located.get(symbol_id)
