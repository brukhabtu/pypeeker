"""In-memory overlay over an :class:`IndexStore` for disk-free simulation.

The composite batch planner simulates whole fix pipelines without touching
disk: mutate file bytes in memory, re-bind the mutated files in memory, and
query the simulated world through the normal engine/resolver. Because
``bind()`` is pure (adapter.parse + bind from bytes) and the query engine
reads exclusively through its store, layering an overlay store over the real
one is sufficient — no engine or resolver changes are required.

:class:`OverlayIndexStore` wraps a base :class:`IndexStore` (composition, not
inheritance) and satisfies the full store surface consumers use:
``project_root``, ``load``, ``save``, ``remove``, ``is_stale``,
``list_indexed_files``, and ``compute_file_hash``. Two layers sit on top of
the base store:

* a **file-bytes layer** (``write_file`` / ``delete_file`` / ``read_file``)
  that shadows the on-disk tree under ``base.project_root``; and
* an **index layer** where ``save`` / ``remove`` operate on an in-memory dict,
  reading through to the base store for untouched files.

Nothing here writes to disk or mutates the base store: the base is strictly
read-only from the overlay's point of view.

Re-binding overlay content into an in-memory :class:`FileIndex` requires the
adapter/binder, which the storage package may not import (import-boundaries:
``storage = ["models"]``); see :mod:`pypeeker.refactor.simulate` for the
``rebind`` convenience built on top of this class.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from pypeeker.models.index import FileIndex
from pypeeker.storage.index_store import INDEX_DIR, SEMANTIC_TOOL_DIR, IndexStore


class OverlayIndexStore:
    """An :class:`IndexStore`-compatible view layering in-memory state over a base store.

    Reads prefer the overlay (both file bytes and indexes) and fall back to
    the base store / the on-disk tree. Writes (``write_file``, ``delete_file``,
    ``save``, ``remove``) only ever touch the overlay's own dictionaries, so
    the base store and the working tree stay byte-for-byte untouched.
    """

    def __init__(self, base: IndexStore) -> None:
        self._base = base
        # File-bytes layer: overlaid contents and tombstones for deletions.
        self._files: dict[str, bytes] = {}
        self._deleted_files: set[str] = set()
        # Index layer: overlaid FileIndex entries and tombstones for removals.
        self._indexes: dict[str, FileIndex] = {}
        self._removed_indexes: set[str] = set()

    @property
    def base(self) -> IndexStore:
        """The wrapped base store (never mutated by the overlay)."""
        return self._base

    @property
    def project_root(self) -> Path:
        """Directory the index is anchored to — the base store's project root."""
        return self._base.project_root

    # ------------------------------------------------------------------
    # File-bytes layer
    # ------------------------------------------------------------------

    def write_file(self, source_path: str, content: bytes) -> None:
        """Set the overlaid bytes for ``source_path`` (project-root-relative).

        Subsequent ``read_file``/``is_stale`` calls see this content instead
        of whatever is (or isn't) on disk. Clears any prior overlay deletion
        of the same path. The on-disk file is never touched.
        """
        self._files[source_path] = content
        self._deleted_files.discard(source_path)

    def delete_file(self, source_path: str) -> None:
        """Mark ``source_path`` as deleted in the overlay.

        ``read_file`` raises ``FileNotFoundError`` for it afterwards even if
        the file exists on disk; ``is_stale`` reports it stale. The on-disk
        file is never touched.
        """
        self._files.pop(source_path, None)
        self._deleted_files.add(source_path)

    def read_file(self, source_path: str) -> bytes:
        """Bytes of ``source_path``: overlay first, then disk under the project root.

        Raises ``FileNotFoundError`` when the path was deleted in the overlay
        or is absent from both layers.
        """
        overlaid = self._files.get(source_path)
        if overlaid is not None:
            return overlaid
        if source_path in self._deleted_files:
            raise FileNotFoundError(source_path)
        return (self._base.project_root / source_path).read_bytes()

    def file_exists(self, source_path: str) -> bool:
        """True when ``source_path`` is readable through the overlay view."""
        if source_path in self._files:
            return True
        if source_path in self._deleted_files:
            return False
        return (self._base.project_root / source_path).is_file()

    # ------------------------------------------------------------------
    # Index layer (the IndexStore contract)
    # ------------------------------------------------------------------

    def save(self, file_index: FileIndex) -> Path:
        """Record ``file_index`` in the overlay; nothing is written to disk.

        Returns the path the index *would* occupy on disk, mirroring
        :meth:`IndexStore.save` so callers relying on the return value keep
        working. Clears any prior overlay removal of the same entry.
        """
        self._indexes[file_index.file_path] = file_index
        self._removed_indexes.discard(file_index.file_path)
        return (
            self._base.project_root
            / SEMANTIC_TOOL_DIR
            / INDEX_DIR
            / (file_index.file_path + ".json")
        )

    def load(self, source_path: str) -> FileIndex | None:
        """Load the index for ``source_path``: overlay first, then the base store.

        Returns ``None`` for entries removed in the overlay even when the base
        store still has them.
        """
        overlaid = self._indexes.get(source_path)
        if overlaid is not None:
            return overlaid
        if source_path in self._removed_indexes:
            return None
        return self._base.load(source_path)

    def remove(self, source_path: str) -> None:
        """Remove the index entry in the overlay only; the base store keeps its copy."""
        self._indexes.pop(source_path, None)
        self._removed_indexes.add(source_path)

    def is_stale(self, source_path: str) -> bool:
        """True if the overlay-visible content changed since indexing (or no index).

        Hashing uses the overlay bytes when the file is overlaid, otherwise the
        on-disk content — so after :meth:`write_file` a path reads stale until
        the simulator re-binds it and saves the in-memory index.
        """
        index = self.load(source_path)
        if index is None:
            return True
        try:
            content = self.read_file(source_path)
        except FileNotFoundError:
            return True
        return hashlib.sha256(content).hexdigest() != index.file_hash

    def list_indexed_files(self) -> list[str]:
        """Base store's indexed files plus overlay additions, minus overlay removals."""
        files = set(self._base.list_indexed_files())
        files -= self._removed_indexes
        files |= set(self._indexes)
        return sorted(files)

    @staticmethod
    def compute_file_hash(file_path: Path) -> str:
        """SHA-256 hash of an on-disk file's contents (same as the base store).

        Static and disk-based by contract; overlay-aware hashing happens in
        :meth:`is_stale`, which hashes :meth:`read_file` content directly.
        """
        return IndexStore.compute_file_hash(file_path)
