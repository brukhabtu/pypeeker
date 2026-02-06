"""Transaction applier: executes planned refactoring operations."""

from __future__ import annotations

from pathlib import Path

from pypeeker.adapters.python_adapter import PythonAdapter
from pypeeker.binder.binder import Binder
from pypeeker.models.transaction import EditEntry, TransactionStatus
from pypeeker.storage.store import IndexStore


class ApplyError(Exception):
    """Raised when transaction application fails."""


class TransactionApplier:
    """Applies a planned transaction to the filesystem.

    Execution strategy:
    1. Load the transaction (header + edits)
    2. Verify all file hashes (conflict detection)
    3. Group edits by file
    4. For each file: write modified content to a temp file
    5. Swap all temp files to real locations (atomic per-file)
    6. Re-index affected files
    7. Clean up: remove transaction file and temps
    """

    def __init__(self, store: IndexStore) -> None:
        self._store = store

    def apply(self, tx_id: str) -> dict:
        """Apply a transaction. Returns a result dict for JSON output."""
        # 1. Load transaction
        result = self._store.load_transaction(tx_id)
        if result is None:
            raise ApplyError(f"Transaction not found: {tx_id}")
        header, edits = result

        if header.status != TransactionStatus.PENDING:
            raise ApplyError(
                f"Transaction {tx_id} is not pending (status: {header.status.value})"
            )

        if not edits:
            raise ApplyError(f"Transaction {tx_id} has no edits")

        # 2. Verify file hashes (conflict detection)
        self._verify_hashes(edits)

        # 3. Group edits by file
        edits_by_file: dict[str, list[EditEntry]] = {}
        for edit in edits:
            edits_by_file.setdefault(edit.file, []).append(edit)

        # 4-5. Apply edits: write temp files, then swap
        temp_files: dict[str, Path] = {}
        backup_contents: dict[str, bytes] = {}
        swapped: list[str] = []

        try:
            # Phase 1: Write temp files with modified content
            for file_path, file_edits in edits_by_file.items():
                source_file = self._store.project_root / file_path
                original_content = source_file.read_bytes()
                backup_contents[file_path] = original_content

                modified_content = self._apply_edits_to_content(
                    original_content, file_edits
                )

                temp_path = source_file.with_suffix(source_file.suffix + ".tmp")
                temp_path.write_bytes(modified_content)
                temp_files[file_path] = temp_path

            # Phase 2: Swap all temp files to real locations
            for file_path, temp_path in temp_files.items():
                source_file = self._store.project_root / file_path
                temp_path.replace(source_file)
                swapped.append(file_path)

        except Exception as e:
            self._rollback(swapped, backup_contents)
            self._cleanup_temps(temp_files)
            raise ApplyError(f"Apply failed, rolled back: {e}") from e

        # 6. Re-index affected files
        reindexed = self._reindex_files(list(edits_by_file.keys()))

        # 7. Clean up transaction file
        self._store.remove_transaction(tx_id)

        return {
            "tx_id": tx_id,
            "status": "applied",
            "files_modified": sorted(edits_by_file.keys()),
            "files_reindexed": reindexed,
        }

    def _verify_hashes(self, edits: list[EditEntry]) -> None:
        """Verify all file hashes match what was recorded at plan time."""
        checked: set[str] = set()
        for edit in edits:
            if edit.file in checked:
                continue
            checked.add(edit.file)

            source_file = self._store.project_root / edit.file
            if not source_file.exists():
                raise ApplyError(f"File not found: {edit.file}")

            current_hash = IndexStore.compute_file_hash(source_file)
            if current_hash != edit.file_hash:
                raise ApplyError(
                    f"File '{edit.file}' has been modified since plan was created. "
                    "Re-run plan-rename to create a new plan."
                )

    @staticmethod
    def _apply_edits_to_content(
        content: bytes, edits: list[EditEntry]
    ) -> bytes:
        """Apply edits to file content, bottom-to-top.

        Sorts edits by start offset descending so that applying
        one edit does not shift the byte offsets of subsequent edits.
        """
        sorted_edits = sorted(edits, key=lambda e: e.start, reverse=True)
        result = bytearray(content)

        for edit in sorted_edits:
            actual = result[edit.start : edit.end]
            expected = edit.old.encode("utf-8")
            if actual != expected:
                raise ApplyError(
                    f"Content mismatch in {edit.file} at offset {edit.start}: "
                    f"expected {edit.old!r}, found {actual.decode('utf-8', errors='replace')!r}"
                )
            result[edit.start : edit.end] = edit.new.encode("utf-8")

        return bytes(result)

    def _rollback(
        self, swapped: list[str], backups: dict[str, bytes]
    ) -> None:
        """Restore files that were already swapped."""
        for file_path in swapped:
            source_file = self._store.project_root / file_path
            if file_path in backups:
                source_file.write_bytes(backups[file_path])

    @staticmethod
    def _cleanup_temps(temp_files: dict[str, Path]) -> None:
        """Remove any temp files that were not swapped."""
        for temp_path in temp_files.values():
            if temp_path.exists():
                temp_path.unlink()

    def _reindex_files(self, file_paths: list[str]) -> list[str]:
        """Re-index affected files after rename is applied."""
        adapter = PythonAdapter()
        reindexed: list[str] = []

        for file_path in file_paths:
            source_file = self._store.project_root / file_path
            if not source_file.exists():
                continue
            try:
                source = source_file.read_bytes()
                tree = adapter.parse(source)
                binder = Binder(adapter, file_path, source)
                file_index = binder.bind(tree.root_node)
                self._store.save(file_index)
                reindexed.append(file_path)
            except Exception:
                pass

        return sorted(reindexed)
