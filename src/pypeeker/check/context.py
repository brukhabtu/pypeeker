"""Project-scoped context handed to cross-file check rules.

Per-file rules see one :class:`~pypeeker.models.index.FileIndex` at a time,
which structurally rules out anything that needs the resolver, the symbol
tree, or a second file. :class:`CheckContext` closes that gap: it carries
every index under ``config.src`` plus lazily-built shared analysis structures,
so a project-scoped rule pays only for what it touches.
"""

from __future__ import annotations

from pypeeker.models import FileIndex, TreeIndex
from pypeeker.resolve import CrossModuleResolver
from pypeeker.storage import IndexStore
from pypeeker.treebuild import build_tree


class CheckContext:
    """Everything a project-scoped rule may need, built once per check run.

    Attributes:
        store:   the :class:`IndexStore`, for rules that need raw file access.
        indexes: every :class:`FileIndex` under ``config.src`` — the same set
                 the per-file rules iterate.

    The resolver and tree are built lazily on first access and shared between
    all project rules in the run, so runs with only per-file rules (or project
    rules that don't need them) never pay for their construction.
    """

    def __init__(self, store: IndexStore, indexes: list[FileIndex]) -> None:
        self.store = store
        self.indexes: tuple[FileIndex, ...] = tuple(indexes)
        self._resolver: CrossModuleResolver | None = None
        self._tree: TreeIndex | None = None

    @property
    def resolver(self) -> CrossModuleResolver:
        """Shared :class:`CrossModuleResolver` over all indexes (lazy)."""
        if self._resolver is None:
            self._resolver = CrossModuleResolver(list(self.indexes))
        return self._resolver

    @property
    def tree(self) -> TreeIndex:
        """Package/module symbol tree over all indexes (lazy, in-memory).

        Built fresh from the indexes via :func:`pypeeker.treebuild.build_tree`
        rather than read from the persisted tree cache, so it is always
        consistent with ``indexes`` even when the on-disk tree is stale.
        """
        if self._tree is None:
            self._tree = build_tree(list(self.indexes))
        return self._tree
