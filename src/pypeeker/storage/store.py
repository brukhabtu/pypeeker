"""Per-file index storage."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pypeeker.models.index import FileIndex
from pypeeker.models.transaction import EditEntry, TransactionHeader

SEMANTIC_TOOL_DIR = ".semantic-tool"
INDEX_DIR = "index"
TRANSACTIONS_DIR = "transactions"


class IndexStore:
    """Manages per-file JSON indexes in the .semantic-tool/index/ directory."""

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
        index_path.write_text(file_index.model_dump_json(indent=2))
        return index_path

    def load(self, source_path: str) -> FileIndex | None:
        """Load the index for a source file, or None if not indexed."""
        index_path = self._source_to_index_path(source_path)
        if not index_path.exists():
            return None
        data = index_path.read_text()
        return FileIndex.model_validate_json(data)

    def is_stale(self, source_path: str) -> bool:
        """Check if the index is stale (file changed or not indexed).

        Returns True if the file needs re-indexing.
        """
        index = self.load(source_path)
        if index is None:
            return True
        source_file = self._project_root / source_path
        if not source_file.exists():
            return True
        current_hash = self.compute_file_hash(source_file)
        return current_hash != index.file_hash

    def list_indexed_files(self) -> list[str]:
        """List all source files that have been indexed."""
        if not self._index_root.exists():
            return []
        files: list[str] = []
        for index_file in self._index_root.rglob("*.json"):
            # Strip the .json suffix and make relative to index root
            relative = index_file.relative_to(self._index_root)
            source_path = str(relative).removesuffix(".json")
            files.append(source_path)
        return sorted(files)

    def remove(self, source_path: str) -> None:
        """Remove the index for a source file."""
        index_path = self._source_to_index_path(source_path)
        if index_path.exists():
            index_path.unlink()

    def _source_to_index_path(self, source_path: str) -> Path:
        """Map source file path to .semantic-tool/index/... path."""
        return self._index_root / (source_path + ".json")

    # --- Transaction persistence ---

    @property
    def transactions_root(self) -> Path:
        return self._project_root / SEMANTIC_TOOL_DIR / TRANSACTIONS_DIR

    def save_transaction(
        self, header: TransactionHeader, edits: list[EditEntry]
    ) -> Path:
        """Write a transaction as JSONL. First line is header, rest are edits."""
        tx_dir = self.transactions_root
        tx_dir.mkdir(parents=True, exist_ok=True)
        tx_path = tx_dir / f"{header.tx_id}.jsonl"
        with tx_path.open("w") as f:
            f.write(header.model_dump_json() + "\n")
            for edit in edits:
                f.write(edit.model_dump_json() + "\n")
        return tx_path

    def load_transaction(
        self, tx_id: str
    ) -> tuple[TransactionHeader, list[EditEntry]] | None:
        """Load a transaction from JSONL. Returns (header, edits) or None."""
        tx_path = self.transactions_root / f"{tx_id}.jsonl"
        if not tx_path.exists():
            return None
        lines = tx_path.read_text().strip().split("\n")
        if not lines:
            return None
        header = TransactionHeader.model_validate_json(lines[0])
        edits = [EditEntry.model_validate_json(line) for line in lines[1:]]
        return header, edits

    def remove_transaction(self, tx_id: str) -> None:
        """Delete a transaction file."""
        tx_path = self.transactions_root / f"{tx_id}.jsonl"
        if tx_path.exists():
            tx_path.unlink()

    def list_transactions(self) -> list[str]:
        """List all transaction IDs."""
        if not self.transactions_root.exists():
            return []
        return sorted(p.stem for p in self.transactions_root.glob("*.jsonl"))

    @staticmethod
    def compute_file_hash(file_path: Path) -> str:
        """SHA-256 hash of a file's contents."""
        return hashlib.sha256(file_path.read_bytes()).hexdigest()
