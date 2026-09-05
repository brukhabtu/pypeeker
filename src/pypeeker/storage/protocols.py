"""Structural contracts for the store surfaces consumers read through.

:class:`~pypeeker.storage.index_store.IndexStore` is the disk-backed store
and :class:`~pypeeker.storage.overlay.OverlayIndexStore` the in-memory
simulation view layered over it. They share no base class — the overlay is
composition, not inheritance — so the contract a consumer (``query``,
``analysis``, ``intents``) actually relies on is *structural*: the
members named on :class:`IndexStoreLike`, which the overlay's module
docstring has always enumerated in prose. Naming the Protocol lets those
consumers annotate against the surface they use rather than the concrete
disk-backed class they happen to receive most often.

:class:`TreeStoreLike` is the same idea for the tree artifact:
:class:`~pypeeker.storage.tree_store.TreeStore` (disk) and
:class:`~pypeeker.storage.tree_store.InMemoryTreeStore` (simulation) share
only the ``save``/``load`` pair.

Both are ``runtime_checkable`` so a test can assert the concrete stores
still satisfy them; no production code should branch on ``isinstance``
against these — that would defeat the point of a structural contract.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Protocol, runtime_checkable

from pypeeker.models import FileIndex, TreeIndex


@runtime_checkable
class TreeStoreLike(Protocol):
    """The ``save``/``load`` pair a cross-file symbol tree is persisted through."""

    def save(self, tree: TreeIndex) -> Path:
        """Persist ``tree`` and return the path it occupies (or would occupy)."""
        ...

    def load(self) -> TreeIndex | None:
        """Return the persisted tree, or ``None`` when none has been built."""
        ...


@runtime_checkable
class IndexStoreLike(Protocol):
    """The per-file index store surface consumers read and write through.

    Exactly the members :class:`~pypeeker.storage.overlay.OverlayIndexStore`
    mirrors from :class:`~pypeeker.storage.index_store.IndexStore`; the
    overlay-only mutation-record accessors (``write_file``, ``delete_file``,
    ``overlaid_files``, ``deleted_files``, ``base_preimages``) are not part
    of this contract because no read-side consumer depends on them.
    """

    @property
    def project_root(self) -> Path:
        """Directory the index is anchored to (the project root)."""
        ...

    def default_tree_store(self) -> TreeStoreLike:
        """Return the tree store this index store defaults to when none is injected."""
        ...

    def save(self, file_index: FileIndex) -> Path:
        """Record ``file_index`` and return the path it occupies (or would occupy)."""
        ...

    def load(self, source_path: str) -> FileIndex | None:
        """Return the index for ``source_path``, or ``None`` if not indexed."""
        ...

    def remove(self, source_path: str) -> None:
        """Drop the index entry for ``source_path``."""
        ...

    def read_file(self, source_path: str) -> bytes:
        """Return the bytes this store serves for ``source_path``."""
        ...

    def file_exists(self, source_path: str) -> bool:
        """Return True when ``source_path`` is readable through this store."""
        ...

    def file_hash(self, source_path: str) -> str:
        """Return the SHA-256 of the bytes this store serves for ``source_path``."""
        ...

    def is_stale(self, source_path: str) -> bool:
        """Return True if ``source_path`` changed since indexing, or was never indexed."""
        ...

    def list_indexed_files(self) -> list[str]:
        """Return every indexed source path, sorted."""
        ...

    def iter_unindexed_source_files(self) -> Iterator[tuple[str, bytes]]:
        """Yield ``(path, bytes)`` for ``.py`` files under the root with no index."""
        ...

    @staticmethod
    def compute_file_hash(file_path: Path) -> str:
        """Return the SHA-256 of an on-disk file's contents."""
        ...
