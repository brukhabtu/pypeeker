"""Per-file index storage.

Manages the ``.semantic-tool/index/*.json`` files produced by binding source
files. Separate from :class:`pypeeker.storage.transaction_store.TransactionStore`
which handles the refactor-transaction JSONL files.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from pypeeker.models.index import FileIndex
from pypeeker.models.serialize import from_json, to_json

SEMANTIC_TOOL_DIR = ".semantic-tool"
INDEX_DIR = "index"


class IndexStore:
    """Per-file JSON indexes under ``.semantic-tool/index/``."""

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root
        self._index_root = project_root / SEMANTIC_TOOL_DIR / INDEX_DIR

    @property
    def project_root(self) -> Path:
        return self._project_root

    def save(self, file_index: FileIndex) -> Path:
        """Save a FileIndex to disk.

        Maps source path to index path:
            src/auth/service.py -> .semantic-tool/index/src/auth/service.py.json
        """
        index_path = self._source_to_index_path(file_index.file_path)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(to_json(file_index, indent=2))
        return index_path

    def load(self, source_path: str) -> FileIndex | None:
        """Load the index for a source file, or None if not indexed."""
        index_path = self._source_to_index_path(source_path)
        if not index_path.exists():
            return None
        return from_json(FileIndex, index_path.read_text())

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
        index_path = self._source_to_index_path(source_path)
        if index_path.exists():
            index_path.unlink()

    def _source_to_index_path(self, source_path: str) -> Path:
        return self._index_root / (source_path + ".json")

    @staticmethod
    def compute_file_hash(file_path: Path) -> str:
        """SHA-256 hash of a file's contents."""
        return hashlib.sha256(file_path.read_bytes()).hexdigest()
