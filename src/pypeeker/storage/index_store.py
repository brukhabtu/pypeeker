"""Per-file index storage.

Manages the ``.pypeeker/index/*.json`` files produced by binding source
files. Separate from :class:`pypeeker.storage.transaction_store.TransactionStore`
which handles the refactor-transaction JSONL files.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from pypeeker.models import FileIndex, from_json, to_json

STORAGE_DIR = ".pypeeker"
LEGACY_STORAGE_DIR = ".semantic-tool"
INDEX_DIR = "index"


def resolve_storage_root(project_root: Path) -> Path:
    """Absolute path to a project's pypeeker storage directory.

    Prefers ``.pypeeker``; if that is absent but a pre-rename ``.semantic-tool``
    directory exists, uses the legacy one so existing local indexes,
    transactions, and baselines keep working without a manual move (read
    fallback, no split state — both reads and writes go to the same resolved
    dir). A project with neither gets ``.pypeeker``.
    """
    new = project_root / STORAGE_DIR
    if new.exists():
        return new
    legacy = project_root / LEGACY_STORAGE_DIR
    if legacy.exists():
        return legacy
    return new


class IndexStore:
    """Per-file JSON indexes under ``.pypeeker/index/`` (see resolve_storage_root)."""

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root
        self._index_root = resolve_storage_root(project_root) / INDEX_DIR
        # In-process cache of parsed indexes. Analysis (call graph, per-function
        # contexts) loads the same files repeatedly; without this, every load
        # re-reads and re-parses JSON. Kept consistent via save()/remove().
        # This is the single read-through cache for FileIndex objects: callers
        # (e.g. SemanticQueryEngine) read through load() rather than keeping
        # their own per-file caches, so reads observe writes made through the
        # same store instance.
        self._cache: dict[str, FileIndex] = {}

    @property
    def project_root(self) -> Path:
        """Directory the index is anchored to (the project root)."""
        return self._project_root

    def save(self, file_index: FileIndex) -> Path:
        """Save a FileIndex to disk.

        Maps source path to index path:
            src/auth/service.py -> .pypeeker/index/src/auth/service.py.json
        """
        index_path = self._source_to_index_path(file_index.file_path)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(to_json(file_index, indent=2))
        self._cache[file_index.file_path] = file_index
        return index_path

    def load(self, source_path: str) -> FileIndex | None:
        """Load the index for a source file, or None if not indexed.

        Parsed indexes are cached in-process; the cache is invalidated by
        :meth:`save` and :meth:`remove`.
        """
        cached = self._cache.get(source_path)
        if cached is not None:
            return cached
        index_path = self._source_to_index_path(source_path)
        if not index_path.exists():
            return None
        index = from_json(FileIndex, index_path.read_text())
        self._cache[source_path] = index
        return index

    def read_file(self, source_path: str) -> bytes:
        """Bytes of ``source_path`` (project-root-relative), read from disk."""
        return (self._project_root / source_path).read_bytes()

    def file_exists(self, source_path: str) -> bool:
        """True when ``source_path`` is a regular file under the project root."""
        return (self._project_root / source_path).is_file()

    def file_hash(self, source_path: str) -> str:
        """SHA-256 hash of the bytes this store serves for ``source_path``.

        Unlike the static :meth:`compute_file_hash`, this hashes through
        :meth:`read_file` — for :class:`IndexStore` that is the same disk
        read, but the method exists so callers can be store-agnostic (see
        :class:`~pypeeker.storage.overlay.OverlayIndexStore`).
        """
        return hashlib.sha256(self.read_file(source_path)).hexdigest()

    def is_stale(self, source_path: str) -> bool:
        """True if the file changed since indexing, or was never indexed."""
        index = self.load(source_path)
        if index is None:
            return True
        source_file = self._project_root / source_path
        if not source_file.exists():
            return True
        return self.compute_file_hash(source_file) != index.file_hash

    def list_indexed_files(self) -> list[str]:
        """List all source files that have been indexed."""
        if not self._index_root.exists():
            return []
        files: list[str] = []
        for index_file in self._index_root.rglob("*.json"):
            relative = index_file.relative_to(self._index_root)
            files.append(str(relative).removesuffix(".json"))
        return sorted(files)

    def remove(self, source_path: str) -> None:
        """Remove the index for a source file."""
        self._cache.pop(source_path, None)
        index_path = self._source_to_index_path(source_path)
        if index_path.exists():
            index_path.unlink()

    def _source_to_index_path(self, source_path: str) -> Path:
        return self._index_root / (source_path + ".json")

    @staticmethod
    def compute_file_hash(file_path: Path) -> str:
        """SHA-256 hash of a file's contents."""
        return hashlib.sha256(file_path.read_bytes()).hexdigest()
