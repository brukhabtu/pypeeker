"""Transaction applier: executes planned refactoring operations."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pypeeker.adapters.python_adapter import PythonAdapter
from pypeeker.binder.binder import bind
from pypeeker.models.transaction import EditEntry, FileRenameEntry, TransactionStatus
from pypeeker.paths import module_path_from
from pypeeker.project import load_src_roots
from pypeeker.storage import IndexStore, TransactionStore


class ApplyError(Exception):
    """Raised when transaction application fails."""


class RollbackError(Exception):
    """Raised when transaction rollback fails."""


class TransactionApplier:
    """Applies a planned transaction to the filesystem.

    Execution strategy:
    1. Load the transaction (header + edits)
    2. Verify all file hashes (conflict detection)
    3. Group edits by file
    4. For each file: write modified content to a temp file
    5. Swap all temp files to real locations (atomic per-file)
    6. Re-index affected files
    7. Mark the transaction APPLIED (retained on disk for rollback)

    On a mid-apply failure the already-swapped files are restored and the
    transaction is marked FAILED. Pre-flight failures (transaction not
    found, not pending, no edits, hash mismatch) leave the transaction
    PENDING since nothing was touched. Only PENDING transactions can be
    applied.

    APPLIED transactions are retained on disk and can later be reverted
    with :meth:`rollback`, which restores the stored ``old`` text and
    marks the transaction ROLLED_BACK.
    """

    def __init__(
        self,
        index_store: IndexStore,
        transaction_store: TransactionStore,
    ) -> None:
        self._index_store = index_store
        self._transaction_store = transaction_store

    def apply(self, tx_id: str) -> dict:
        """Apply a transaction. Returns a result dict for JSON output."""
        # 1. Load transaction
        result = self._transaction_store.load(tx_id)
        if result is None:
            raise ApplyError(f"Transaction not found: {tx_id}")
        header, edits, file_rename = result

        if header.status != TransactionStatus.PENDING:
            raise ApplyError(
                f"Transaction {tx_id} is not pending (status: {header.status.value})"
            )

        if not edits and not file_rename:
            raise ApplyError(f"Transaction {tx_id} has no edits")

        # 2. Verify file hashes (conflict detection)
        self._verify_hashes(edits, file_rename)

        # 3. Group edits by file
        edits_by_file: dict[str, list[EditEntry]] = {}
        for edit in edits:
            edits_by_file.setdefault(edit.file, []).append(edit)

        # 4-5. Apply edits: write temp files, then swap
        temp_files: dict[str, Path] = {}
        backup_contents: dict[str, bytes] = {}
        swapped: list[str] = []
        renamed_file: tuple[str, str] | None = None

        try:
            # Phase 1: Write temp files with modified content
            for file_path, file_edits in edits_by_file.items():
                source_file = self._index_store.project_root / file_path
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
                source_file = self._index_store.project_root / file_path
                temp_path.replace(source_file)
                swapped.append(file_path)

            # Phase 3: Apply file rename if present
            if file_rename:
                renamed_file = self._apply_file_rename(file_rename)

        except Exception as e:
            self._rollback(swapped, backup_contents)
            self._cleanup_temps(temp_files)
            if renamed_file:
                self._rollback_file_rename(renamed_file)
            self._transaction_store.update_status(tx_id, TransactionStatus.FAILED)
            raise ApplyError(f"Apply failed, rolled back: {e}") from e

        # 6. Re-index affected files
        files_to_reindex = list(edits_by_file.keys())
        if renamed_file:
            # Remove old path, add new path
            old_path, new_path = renamed_file
            if old_path in files_to_reindex:
                files_to_reindex.remove(old_path)
            files_to_reindex.append(new_path)
            # Remove old index
            self._index_store.remove(old_path)

        reindexed, reindex_failed = self._reindex_files(files_to_reindex)

        # 7. Mark the transaction applied; keep it on disk for rollback
        self._transaction_store.update_status(tx_id, TransactionStatus.APPLIED)

        files_modified = sorted(edits_by_file.keys())
        if renamed_file:
            files_modified.append(f"{renamed_file[0]} -> {renamed_file[1]}")

        return {
            "tx_id": tx_id,
            "status": "applied",
            "files_modified": files_modified,
            "files_reindexed": reindexed,
            "files_reindex_failed": reindex_failed,
        }

    def rollback(self, tx_id: str) -> dict:
        """Roll back an APPLIED transaction. Returns a result dict for JSON output.

        Rollback strategy:
        1. Load the transaction; only APPLIED transactions can be rolled back
        2. For each edited file, verify it still holds the post-apply content
           (the post-apply span of every edit must contain the replacement
           text, and the restored bytes must hash back to the plan-time file
           hash) — refuse before touching anything if not (no partial
           rollback)
        3. Splice the stored ``old`` text back into every file
        4. Reverse the file rename if present (new_path -> old_path)
        5. Re-index affected files and mark the transaction ROLLED_BACK
        """
        # 1. Load transaction
        result = self._transaction_store.load(tx_id)
        if result is None:
            raise RollbackError(f"Transaction not found: {tx_id}")
        header, edits, file_rename = result

        if header.status != TransactionStatus.APPLIED:
            raise RollbackError(
                f"Transaction {tx_id} is not applied (status: "
                f"{header.status.value}); only applied transactions can be "
                "rolled back"
            )

        # 2. Group edits by file; a rename moves the file, so edited paths
        # (recorded pre-rename) are read at their current location.
        edits_by_file: dict[str, list[EditEntry]] = {}
        for edit in edits:
            edits_by_file.setdefault(edit.file, []).append(edit)

        def current_path(file_path: str) -> str:
            if file_rename and file_path == file_rename.old_path:
                return file_rename.new_path
            return file_path

        # Verify everything and compute restored contents BEFORE writing
        # anything: a mismatch must refuse the whole rollback.
        restored_contents: dict[str, bytes] = {}  # keyed by current path
        for file_path, file_edits in edits_by_file.items():
            source_file = self._index_store.project_root / current_path(file_path)
            if not source_file.exists():
                raise RollbackError(f"File not found: {current_path(file_path)}")

            restored = self._revert_edits_in_content(
                source_file.read_bytes(), file_edits
            )
            restored_hash = hashlib.sha256(restored).hexdigest()
            if restored_hash != file_edits[0].file_hash:
                raise RollbackError(
                    f"File '{current_path(file_path)}' has been modified since "
                    "the transaction was applied; refusing to roll back."
                )
            restored_contents[current_path(file_path)] = restored

        if file_rename:
            new_file = self._index_store.project_root / file_rename.new_path
            old_file = self._index_store.project_root / file_rename.old_path
            if not new_file.exists():
                raise RollbackError(f"File not found: {file_rename.new_path}")
            if old_file.exists():
                raise RollbackError(
                    f"Cannot reverse rename: '{file_rename.old_path}' already "
                    "exists"
                )
            if file_rename.old_path not in edits_by_file:
                # Rename-only file: its content was untouched by apply, so it
                # must still hash to the plan-time hash.
                current_hash = IndexStore.compute_file_hash(new_file)
                if current_hash != file_rename.file_hash:
                    raise RollbackError(
                        f"File '{file_rename.new_path}' has been modified "
                        "since the transaction was applied; refusing to roll "
                        "back."
                    )

        # 3. Write restored contents (temp file + atomic swap, as in apply)
        for file_path, restored in restored_contents.items():
            source_file = self._index_store.project_root / file_path
            temp_path = source_file.with_suffix(source_file.suffix + ".tmp")
            temp_path.write_bytes(restored)
            temp_path.replace(source_file)

        # 4. Reverse the file rename
        if file_rename:
            self._rollback_file_rename(
                (file_rename.old_path, file_rename.new_path)
            )

        # 5. Re-index affected files at their restored locations
        files_to_reindex = list(edits_by_file.keys())
        if file_rename:
            if file_rename.old_path not in files_to_reindex:
                files_to_reindex.append(file_rename.old_path)
            self._index_store.remove(file_rename.new_path)

        reindexed, reindex_failed = self._reindex_files(files_to_reindex)

        self._transaction_store.update_status(tx_id, TransactionStatus.ROLLED_BACK)

        files_restored = sorted(edits_by_file.keys())
        if file_rename:
            files_restored.append(
                f"{file_rename.new_path} -> {file_rename.old_path}"
            )

        return {
            "tx_id": tx_id,
            "status": "rolled_back",
            "files_restored": files_restored,
            "files_reindexed": reindexed,
            "files_reindex_failed": reindex_failed,
        }

    def _verify_hashes(
        self, edits: list[EditEntry], file_rename: FileRenameEntry | None = None
    ) -> None:
        """Verify all file hashes match what was recorded at plan time."""
        checked: set[str] = set()
        for edit in edits:
            if edit.file in checked:
                continue
            checked.add(edit.file)

            source_file = self._index_store.project_root / edit.file
            if not source_file.exists():
                raise ApplyError(f"File not found: {edit.file}")

            current_hash = IndexStore.compute_file_hash(source_file)
            if current_hash != edit.file_hash:
                raise ApplyError(
                    f"File '{edit.file}' has been modified since plan was created. "
                    "Re-run plan-rename to create a new plan."
                )

        # Also verify file rename hash if present
        if file_rename and file_rename.old_path not in checked:
            source_file = self._index_store.project_root / file_rename.old_path
            if not source_file.exists():
                raise ApplyError(f"File not found: {file_rename.old_path}")

            current_hash = IndexStore.compute_file_hash(source_file)
            if current_hash != file_rename.file_hash:
                raise ApplyError(
                    f"File '{file_rename.old_path}' has been modified since plan was created. "
                    "Re-run plan-rename to create a new plan."
                )

    def _apply_file_rename(self, file_rename: FileRenameEntry) -> tuple[str, str]:
        """Rename a file. Returns (old_path, new_path)."""
        old_file = self._index_store.project_root / file_rename.old_path
        new_file = self._index_store.project_root / file_rename.new_path

        # Ensure parent directory exists
        new_file.parent.mkdir(parents=True, exist_ok=True)

        # Rename the file
        old_file.rename(new_file)

        return (file_rename.old_path, file_rename.new_path)

    def _rollback_file_rename(self, renamed: tuple[str, str]) -> None:
        """Rollback a file rename."""
        old_path, new_path = renamed
        new_file = self._index_store.project_root / new_path
        old_file = self._index_store.project_root / old_path

        if new_file.exists():
            new_file.rename(old_file)

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

    @staticmethod
    def _revert_edits_in_content(
        content: bytes, edits: list[EditEntry]
    ) -> bytes:
        """Compute pre-apply content from post-apply content.

        Replays the edits in ascending offset order to derive each edit's
        post-apply byte span (earlier replacements in the same file shift
        later offsets by ``len(new) - len(old)``), verifies every span
        still holds the replacement text, then splices the stored ``old``
        text back bottom-to-top so earlier spans stay valid while writing.
        Raises :class:`RollbackError` on any mismatch — the file has been
        modified since apply and must not be partially reverted.
        """
        spans: list[tuple[int, int, EditEntry]] = []  # (post_start, post_end, edit)
        delta = 0
        for edit in sorted(edits, key=lambda e: e.start):
            new_bytes = edit.new.encode("utf-8")
            post_start = edit.start + delta
            spans.append((post_start, post_start + len(new_bytes), edit))
            delta += len(new_bytes) - (edit.end - edit.start)

        result = bytearray(content)
        for post_start, post_end, edit in reversed(spans):
            actual = bytes(result[post_start:post_end])
            if actual != edit.new.encode("utf-8"):
                raise RollbackError(
                    f"File '{edit.file}' has been modified since the "
                    f"transaction was applied: expected {edit.new!r} at "
                    f"offset {post_start}, found "
                    f"{actual.decode('utf-8', errors='replace')!r}. "
                    "Refusing to roll back."
                )
            result[post_start:post_end] = edit.old.encode("utf-8")

        return bytes(result)

    def _rollback(
        self, swapped: list[str], backups: dict[str, bytes]
    ) -> None:
        """Restore files that were already swapped."""
        for file_path in swapped:
            source_file = self._index_store.project_root / file_path
            if file_path in backups:
                source_file.write_bytes(backups[file_path])

    @staticmethod
    def _cleanup_temps(temp_files: dict[str, Path]) -> None:
        """Remove any temp files that were not swapped."""
        for temp_path in temp_files.values():
            if temp_path.exists():
                temp_path.unlink()

    def _reindex_files(
        self, file_paths: list[str]
    ) -> tuple[list[str], list[dict[str, str]]]:
        """Re-index affected files after edits are applied.

        Returns (reindexed, failed) where ``failed`` contains one
        ``{"file": path, "error": message}`` entry per file whose
        re-index raised. The apply itself has already succeeded by this
        point (edits are on disk), so failures are reported rather than
        raised — but they must not be swallowed, since a stale index
        entry silently corrupts every downstream query and plan.
        """
        adapter = PythonAdapter()
        src_roots = load_src_roots(self._index_store.project_root)
        reindexed: list[str] = []
        failed: list[dict[str, str]] = []

        for file_path in file_paths:
            source_file = self._index_store.project_root / file_path
            if not source_file.exists():
                continue
            try:
                source = source_file.read_bytes()
                tree = adapter.parse(source)
                module_path = module_path_from(file_path, src_roots)
                file_index = bind(
                    adapter, file_path, source, tree.root_node, module_path=module_path
                )
                self._index_store.save(file_index)
                reindexed.append(file_path)
            except Exception as e:
                failed.append({"file": file_path, "error": str(e)})

        return sorted(reindexed), sorted(failed, key=lambda f: f["file"])
