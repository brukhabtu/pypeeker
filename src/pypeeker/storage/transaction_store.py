"""Transaction storage.

Manages the ``.semantic-tool/transactions/*.jsonl`` files written by
:mod:`pypeeker.refactor.planner` and consumed by
:mod:`pypeeker.refactor.applier`.
"""

from __future__ import annotations

import json
from pathlib import Path

from pypeeker.models.serialize import from_dict, from_json, to_json
from pypeeker.models.transaction import (
    EditEntry,
    EditOp,
    FileRenameEntry,
    TransactionHeader,
)

SEMANTIC_TOOL_DIR = ".semantic-tool"
TRANSACTIONS_DIR = "transactions"

LoadedTransaction = tuple[TransactionHeader, list[EditEntry], FileRenameEntry | None]


class TransactionStore:
    """Refactor transactions as JSONL under ``.semantic-tool/transactions/``."""

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root
        self._root = project_root / SEMANTIC_TOOL_DIR / TRANSACTIONS_DIR

    @property
    def project_root(self) -> Path:
        return self._project_root

    @property
    def root(self) -> Path:
        return self._root

    def save(
        self,
        header: TransactionHeader,
        edits: list[EditEntry],
        file_rename: FileRenameEntry | None = None,
    ) -> Path:
        """Write a transaction as JSONL: header line first, then edit/rename lines."""
        self._root.mkdir(parents=True, exist_ok=True)
        tx_path = self._root / f"{header.tx_id}.jsonl"
        with tx_path.open("w") as f:
            f.write(to_json(header) + "\n")
            for edit in edits:
                f.write(to_json(edit) + "\n")
            if file_rename:
                f.write(to_json(file_rename) + "\n")
        return tx_path

    def load(self, tx_id: str) -> LoadedTransaction | None:
        """Load a transaction. Returns (header, edits, file_rename) or None."""
        tx_path = self._root / f"{tx_id}.jsonl"
        if not tx_path.exists():
            return None
        lines = tx_path.read_text().strip().split("\n")
        if not lines:
            return None
        header = from_json(TransactionHeader, lines[0])
        edits: list[EditEntry] = []
        file_rename: FileRenameEntry | None = None
        for line in lines[1:]:
            data = json.loads(line)
            if data.get("op") == EditOp.RENAME_FILE.value:
                file_rename = from_dict(FileRenameEntry, data)
            else:
                edits.append(from_dict(EditEntry, data))
        return header, edits, file_rename

    def remove(self, tx_id: str) -> None:
        """Delete a transaction file."""
        tx_path = self._root / f"{tx_id}.jsonl"
        if tx_path.exists():
            tx_path.unlink()

    def list(self) -> list[str]:
        """List all transaction IDs."""
        if not self._root.exists():
            return []
        return sorted(p.stem for p in self._root.glob("*.jsonl"))
