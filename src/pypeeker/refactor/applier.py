"""Transaction applier: executes planned refactoring operations."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pypeeker.adapters import PythonAdapter
from pypeeker.binder import bind
from pypeeker.models import (
    EditEntry,
    FileCreateEntry,
    FileDeleteEntry,
    FileRenameEntry,
    TransactionStatus,
)
from pypeeker.paths import module_path_from
from pypeeker.project import load_src_roots
from pypeeker.storage import IndexStore, TransactionStore


# The one path two entries in a single transaction may legitimately share:
# the module-rename shape edits a file's contents and then renames the file.
# Every other pairing is refused by
# :meth:`TransactionApplier._verify_no_path_conflicts`.
_ALLOWED_PATH_SHARING = frozenset({tuple(sorted(("an edit", "a rename source")))})


class ApplyError(Exception):
    """Raised when transaction application fails."""


class RollbackError(Exception):
    """Raised when transaction rollback fails."""


class TransactionApplier:
    """Applies a planned transaction to the filesystem.

    Execution strategy, over three resource kinds (edits, creates, deletes)
    plus at most one rename, ordered so any failure is restorable:
    1. Load the transaction (header + edits + creates + deletes + rename)
    2. Pre-flight: refuse entries that collide with each other on a path
       (two entries can each be individually valid and still leave an
       applied transaction with no inverse), verify edit/delete file hashes
       and each entry's pinned content hash (conflict detection), and check
       that every creation's target does not yet exist — the absence check
       is what makes the failure path's asymmetric unlink-on-failure safe
    3. Stage: write edit temps and creation temps (never to the real path),
       buffer deletion bytes for restore
    4. Commit: swap edit temps, swap creation temps, unlink deletions, then
       the rename
    5. Re-index affected files (deleted paths lose their index entry;
       created/edited paths get a fresh one)
    6. Mark the transaction APPLIED (retained on disk for rollback)

    On a mid-apply failure: already-swapped edits are restored from backup,
    already-unlinked deletions are recreated from backup, already-created
    files are removed (safe only because pre-flight just proved they did
    not pre-exist), the rename is reversed, temps are cleaned up, any
    directory this apply had to conjure — for a creation or for the
    rename's target — is removed, and the transaction is marked FAILED.
    Pre-flight failures (transaction not found, not pending, nothing to do,
    colliding entries, hash mismatch, creation target exists) leave the
    transaction PENDING since nothing was touched. Only PENDING
    transactions can be applied.

    APPLIED transactions are retained on disk and can later be reverted
    with :meth:`rollback`, which restores the stored ``old`` text, removes
    files this transaction created — together with exactly the directories
    its apply recorded conjuring — recreates files it deleted, and marks
    the transaction ROLLED_BACK.
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
        header = result.header
        edits = result.edits
        file_rename = result.file_rename
        creates = result.creates
        deletes = result.deletes

        if header.status != TransactionStatus.PENDING:
            raise ApplyError(
                f"Transaction {tx_id} is not pending (status: {header.status.value})"
            )

        if not edits and not file_rename and not creates and not deletes:
            raise ApplyError(f"Transaction {tx_id} has no edits")

        # 2. Pre-flight: entries must not collide with each other, then
        # each entry is verified against the tree (hashes + creation absence)
        self._verify_no_path_conflicts(edits, file_rename, creates, deletes)
        self._verify_hashes(edits, file_rename, creates, deletes)

        # 3. Group edits by file
        edits_by_file: dict[str, list[EditEntry]] = {}
        for edit in edits:
            edits_by_file.setdefault(edit.file, []).append(edit)

        # 4-5. Stage then commit: edit temps, creation temps, deletion
        # backups; swap edits, swap creations, unlink deletions, rename.
        temp_files: dict[str, Path] = {}
        create_temps: dict[str, Path] = {}
        backup_contents: dict[str, bytes] = {}
        delete_backups: dict[str, bytes] = {}
        swapped: list[str] = []
        created: list[str] = []
        deleted: list[str] = []
        # Directories this apply had to bring into existence for a creation
        # or for a rename's target. They are part of the mutation and must
        # come back out on the failure path, or a FAILED transaction leaves
        # the tree not byte-identical — and under PEP 420 a leftover empty
        # directory is an importable namespace package, not inert clutter.
        created_dirs: list[Path] = []
        # The same directories as project-relative POSIX strings, for the
        # transaction record. Derived inside the try alongside the mkdir
        # that produced them, never after the commit: an entry path that
        # escapes the project root makes the conversion raise, and a raise
        # out here — past the failure handler — would strand a mutated tree
        # under a still-PENDING header with no inverse and no error envelope.
        created_dir_records: list[str] = []
        renamed_file: tuple[str, str] | None = None

        def record_conjured_dirs(conjured: list[Path]) -> None:
            """Merge an already-known conjured-directory batch into both records.

            The in-memory list the failure handler removes and the
            project-relative record the header carries are the same fact, so
            they are derived together, right where each caller learns what
            it conjured (or is about to), and inside the failure handler's
            reach (see ``created_dir_records`` above). Shared by the
            creation path (whose mkdir happens through
            :meth:`_make_parent_dirs`, called just before this) and the
            rename path (whose mkdir happens inside
            :meth:`_apply_file_rename` itself, so this is fed a prediction
            from :meth:`_missing_parent_dirs` taken immediately before that
            call).
            """
            created_dirs.extend(conjured)
            created_dir_records.extend(
                d.relative_to(self._index_store.project_root).as_posix()
                for d in conjured
            )

        try:
            # Phase 1 (stage): write temp files with modified/new content;
            # buffer deletion bytes. Nothing real is touched yet.
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

            for create in creates:
                target = self._index_store.project_root / create.path
                record_conjured_dirs(self._make_parent_dirs(target))
                temp_path = target.with_suffix(target.suffix + ".tmp")
                temp_path.write_bytes(create.content.encode("utf-8"))
                create_temps[create.path] = temp_path

            for delete in deletes:
                source_file = self._index_store.project_root / delete.path
                delete_backups[delete.path] = source_file.read_bytes()

            # Phase 2 (commit): swap edits, swap creations, unlink
            # deletions, then the rename.
            for file_path, temp_path in temp_files.items():
                source_file = self._index_store.project_root / file_path
                temp_path.replace(source_file)
                swapped.append(file_path)

            for create in creates:
                target = self._index_store.project_root / create.path
                create_temps[create.path].replace(target)
                created.append(create.path)

            for delete in deletes:
                target = self._index_store.project_root / delete.path
                target.unlink()
                deleted.append(delete.path)

            if file_rename:
                new_target = self._index_store.project_root / file_rename.new_path
                # Queried before the rename's own mkdir runs (inside
                # _apply_file_rename below) so this sees exactly what that
                # call is about to conjure — no window for the filesystem
                # state to have moved in between.
                record_conjured_dirs(self._missing_parent_dirs(new_target))
                renamed_file = self._apply_file_rename(file_rename)

        except Exception as e:
            self._rollback(swapped, backup_contents)
            self._restore_deleted(deleted, delete_backups)
            self._unlink_created(created)
            self._cleanup_temps(temp_files)
            self._cleanup_temps(create_temps)
            if renamed_file:
                self._rollback_file_rename(renamed_file)
            # Only after every file, temp, and renamed file under them is gone.
            self._remove_dirs(created_dirs)
            self._transaction_store.update_status(tx_id, TransactionStatus.FAILED)
            raise ApplyError(f"Apply failed, rolled back: {e}") from e

        # 6. Re-index affected files. Deleted paths no longer exist, so
        # _reindex_files removes their ghost index entry instead of
        # skipping them; created paths bind like any other new content.
        files_to_reindex = list(edits_by_file.keys()) + created + deleted
        if renamed_file:
            # Remove old path, add new path
            old_path, new_path = renamed_file
            if old_path in files_to_reindex:
                files_to_reindex.remove(old_path)
            files_to_reindex.append(new_path)
            # Remove old index
            self._index_store.remove(old_path)

        reindexed, reindex_failed = self._reindex_files(files_to_reindex)

        # 7. Mark the transaction applied; keep it on disk for rollback. The
        # directories this apply conjured go into the record: rollback runs
        # in a later process and must remove exactly those, never a
        # directory that merely happens to be empty by then -- it may have
        # predated the transaction, in which case it is not ours to remove.
        # The list was built during staging (see ``created_dir_records``);
        # nothing here can fail on a path shape after the commit.
        self._transaction_store.mark_applied(tx_id, created_dir_records)

        files_modified = sorted(edits_by_file.keys())
        if renamed_file:
            files_modified.append(f"{renamed_file[0]} -> {renamed_file[1]}")

        return {
            "tx_id": tx_id,
            "status": "applied",
            "files_modified": files_modified,
            "files_created": sorted(created),
            "files_deleted": sorted(deleted),
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
           hash); for each created file, verify it still hashes to
           ``content_hash``; for each deleted file, verify its path is free
           — refuse before touching anything if not (no partial rollback)
        3. Splice the stored ``old`` text back into every file
        4. Remove files this transaction created; recreate files it deleted,
           byte-for-byte, from their stored pre-image
        5. Reverse the file rename if present (new_path -> old_path), then
           remove exactly the directories the apply recorded conjuring
           (``TransactionHeader.created_dirs``), deepest-first and only
           while still empty and only where they resolve inside the
           project root, now that every file is back in place. A
           header with no record (written before this field existed)
           licenses no directory removal — no evidence, nothing removed.
        6. Re-index affected files and mark the transaction ROLLED_BACK
        """
        # 1. Load transaction
        result = self._transaction_store.load(tx_id)
        if result is None:
            raise RollbackError(f"Transaction not found: {tx_id}")
        header = result.header
        edits = result.edits
        file_rename = result.file_rename
        creates = result.creates
        deletes = result.deletes

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
            """Map a plan-time path to its on-disk location after the rename."""
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

        # Guarded inverses for creates/deletes, verified before any write —
        # parity with the edit/rename checks above. A create rolls back only
        # if untouched since apply (content_hash still matches); a delete
        # rolls back only if its path is now free, mirroring the rename
        # reversal's already-occupied refusal.
        for create in creates:
            target = self._index_store.project_root / create.path
            if not target.exists():
                raise RollbackError(f"File not found: {create.path}")
            current_hash = IndexStore.compute_file_hash(target)
            if current_hash != create.content_hash:
                raise RollbackError(
                    f"File '{create.path}' has been modified since the "
                    "transaction was applied; refusing to roll back."
                )

        for delete in deletes:
            target = self._index_store.project_root / delete.path
            if target.exists():
                raise RollbackError(
                    f"Cannot restore deleted file '{delete.path}': a file "
                    "already exists at that path; refusing to roll back."
                )

        # 3. Write restored contents (temp file + atomic swap, as in apply)
        for file_path, restored in restored_contents.items():
            source_file = self._index_store.project_root / file_path
            temp_path = source_file.with_suffix(source_file.suffix + ".tmp")
            temp_path.write_bytes(restored)
            temp_path.replace(source_file)

        # 4. Undo creates (remove, plus any directory the creation brought
        # into existence) and deletes (recreate byte-for-byte).
        removed_creations: list[str] = []
        for create in creates:
            target = self._index_store.project_root / create.path
            target.unlink()
            removed_creations.append(create.path)

        restored_deletions: list[str] = []
        for delete in deletes:
            target = self._index_store.project_root / delete.path
            self._make_parent_dirs(target)
            target.write_bytes(delete.content.encode("utf-8"))
            restored_deletions.append(delete.path)

        # 5. Reverse the file rename
        if file_rename:
            self._rollback_file_rename(
                (file_rename.old_path, file_rename.new_path)
            )

        # 5b. Remove exactly the directories this transaction's apply
        # recorded conjuring -- not reconstructed by walking up from the
        # removed file. A directory that is merely empty may well have
        # predated the transaction, and rollback does not own it. Last,
        # after every file this rollback puts back is in place, so the
        # only-if-empty guard cannot fire on a directory about to receive a
        # file. A header with no record (``created_dirs is None``) was
        # written by a build predating the field: no evidence, so nothing
        # is removed -- residue, never destruction of what we did not make.
        # Entries that do not resolve inside the project root are dropped
        # by _recorded_created_dirs on the same principle.
        if header.created_dirs is not None:
            self._remove_dirs(self._recorded_created_dirs(header.created_dirs))

        # 6. Re-index affected files at their restored locations. A removed
        # creation loses its index entry (_reindex_files sees it is gone); a
        # restored deletion is re-bound like any other existing file.
        files_to_reindex = (
            list(edits_by_file.keys()) + removed_creations + restored_deletions
        )
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
            "files_created": sorted(restored_deletions),
            "files_deleted": sorted(removed_creations),
            "files_reindexed": reindexed,
            "files_reindex_failed": reindex_failed,
        }

    @staticmethod
    def _verify_no_path_conflicts(
        edits: list[EditEntry],
        file_rename: FileRenameEntry | None = None,
        creates: list[FileCreateEntry] | None = None,
        deletes: list[FileDeleteEntry] | None = None,
    ) -> None:
        """Refuse a transaction whose own entries collide on a path.

        Verifying each entry against the tree is not enough: two entries can
        each pass pre-flight and still destroy each other at commit, ending
        APPLIED with the tree mutated and **no inverse**. An edit plus a
        deletion of the same path both hash-match, then commit swaps the
        edit in and unlinks the file — rollback can never find it again. A
        creation plus a rename onto the same target both pass (the target is
        absent), then the rename silently overwrites the newborn. A deletion
        plus a rename onto the freed path applies cleanly and then refuses
        rollback forever, because restoring the deleted file finds its path
        occupied.

        None of those are recoverable after the fact, so they are refused
        here, before anything is touched: the transaction stays PENDING and
        the tree is untouched. The single exception is editing a file and
        then renaming it — the module-rename shape, where the applier's
        phase order (edits, then rename) is well defined and rollback maps
        the edited path through the rename.
        """
        claims: dict[str, list[str]] = {}
        for path in dict.fromkeys(edit.file for edit in edits):
            claims.setdefault(path, []).append("an edit")
        for create in creates or []:
            claims.setdefault(create.path, []).append("a creation")
        for delete in deletes or []:
            claims.setdefault(delete.path, []).append("a deletion")
        if file_rename:
            claims.setdefault(file_rename.old_path, []).append("a rename source")
            claims.setdefault(file_rename.new_path, []).append("a rename target")

        for path, kinds in sorted(claims.items()):
            if len(kinds) == 1:
                continue
            if tuple(sorted(kinds)) in _ALLOWED_PATH_SHARING:
                continue
            raise ApplyError(
                f"Conflicting entries for '{path}': {' and '.join(sorted(kinds))} "
                "in the same transaction. Such a transaction cannot be rolled "
                "back once applied, so it is refused before anything is touched."
            )

    def _verify_hashes(
        self,
        edits: list[EditEntry],
        file_rename: FileRenameEntry | None = None,
        creates: list[FileCreateEntry] | None = None,
        deletes: list[FileDeleteEntry] | None = None,
    ) -> None:
        """Verify all file hashes match what was recorded at plan time.

        Also verifies the file-lifecycle pre-conditions: every creation's
        target must not yet exist (the pre-image of a creation is absence,
        which is exactly what makes the failure path's asymmetric
        unlink-on-failure for created files safe), and every deletion's
        target must exist and still hash-match, exactly like a text edit.

        And — the check that makes those two meaningful — every entry's
        ``content`` must itself hash to the hash the entry pins. A
        ``FileDeleteEntry`` carries the pre-image as ``str`` while
        ``file_hash`` is the SHA-256 of the on-disk *bytes*; a producer that
        read the file with universal newlines (or any other lossy decode)
        satisfies the on-disk hash check and still restores different bytes
        on rollback, which is silent corruption of the byte-for-byte inverse
        rather than a refusal. The same argument applies to
        ``FileCreateEntry.content_hash``, where a mismatch instead makes
        rollback permanently impossible with a misleading "modified since
        applied" message. Both are refused here, at plan-vs-entry time.
        """
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
                    "Re-run the original command to plan against the current file."
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
                    "Re-run the original command to plan against the current file."
                )

        for create in creates or []:
            self._verify_pinned_content(
                create.path, create.content, create.content_hash, "content_hash"
            )
            target = self._index_store.project_root / create.path
            if target.exists():
                raise ApplyError(
                    f"Cannot create '{create.path}': a file already exists "
                    "at that path. Re-run the original command to plan "
                    "against the current tree."
                )

        for delete in deletes or []:
            self._verify_pinned_content(
                delete.path, delete.content, delete.file_hash, "file_hash"
            )
            source_file = self._index_store.project_root / delete.path
            if not source_file.exists():
                raise ApplyError(f"File not found: {delete.path}")

            current_hash = IndexStore.compute_file_hash(source_file)
            if current_hash != delete.file_hash:
                raise ApplyError(
                    f"File '{delete.path}' has been modified since plan was created. "
                    "Re-run the original command to plan against the current file."
                )

    @staticmethod
    def _verify_pinned_content(
        path: str, content: str, pinned_hash: str, field_name: str
    ) -> None:
        """Refuse an entry whose ``content`` does not hash to its pinned hash.

        The entry's own bytes are the only pre-image rollback has, so a
        mismatch here is not a stale-plan conflict but a malformed entry:
        applying it would write (or restore) bytes the transaction never
        promised. Refusing at pre-flight keeps the transaction PENDING and
        the tree untouched.
        """
        actual = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if actual != pinned_hash:
            raise ApplyError(
                f"Malformed entry for '{path}': its content hashes to "
                f"{actual} but the entry's {field_name} pins {pinned_hash}. "
                "The stored content is the only pre-image rollback has, so "
                "this transaction is refused rather than applied."
            )

    def _apply_file_rename(self, file_rename: FileRenameEntry) -> tuple[str, str]:
        """Rename a file. Returns (old_path, new_path).

        Makes its own parent directory rather than delegating to
        :meth:`_make_parent_dirs`, so the directories it conjures are still
        recorded even though this method's own ``mkdir`` isn't the one
        doing the reporting: ``apply`` calls :meth:`_missing_parent_dirs`
        on this same target immediately before invoking this method, and
        folds that prediction into ``created_dirs`` / ``created_dir_records``
        — so a directory conjured for a rename is undone with it on the
        failure path and on rollback, exactly like one conjured for a
        creation.
        """
        old_file = self._index_store.project_root / file_rename.old_path
        new_file = self._index_store.project_root / file_rename.new_path

        # Ensure parent directory exists (recorded by ``apply`` beforehand;
        # see docstring above).
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

    def _restore_deleted(
        self, deleted: list[str], backups: dict[str, bytes]
    ) -> None:
        """Recreate files that were already unlinked, from their backups.

        Part of the mid-apply failure path: a deletion that already
        committed (the real file is gone) must come back byte-for-byte so a
        FAILED transaction leaves the tree exactly as it found it.
        """
        for file_path in deleted:
            if file_path not in backups:
                continue
            target = self._index_store.project_root / file_path
            self._make_parent_dirs(target)
            target.write_bytes(backups[file_path])

    @staticmethod
    def _missing_parent_dirs(target: Path) -> list[Path]:
        """Ancestors of ``target``'s parent that do not exist yet, outermost first.

        Pure query — no filesystem mutation. Shared by
        :meth:`_make_parent_dirs` (which goes on to create what this
        reports) and by ``apply``'s rename branch, which needs to know what
        :meth:`_apply_file_rename`'s own ``mkdir(parents=True)`` is about
        to conjure *before* that call runs, so the same directories join
        ``created_dirs`` / ``created_dir_records`` the way creation-conjured
        ones do.
        """
        conjured: list[Path] = []
        directory = target.parent
        while not directory.exists():
            conjured.append(directory)
            parent = directory.parent
            if parent == directory:  # pragma: no cover — filesystem root
                break
            directory = parent
        conjured.reverse()  # outermost first
        return conjured

    @staticmethod
    def _make_parent_dirs(target: Path) -> list[Path]:
        """``mkdir -p`` ``target``'s parent, reporting what it had to create.

        Returns the directories that did not exist beforehand (via
        :meth:`_missing_parent_dirs`), outermost first, so a caller undoing
        the write can undo the directories too. A directory conjured for a
        file is part of that file's mutation, and an apply that ends FAILED
        (or a rollback that removes what it created) has not restored the
        tree while one survives. Used by the creation and deletion-restore
        paths, which is also where ``TransactionHeader.created_dirs`` gets
        most of its value from at apply time.
        :meth:`_apply_file_rename` makes its own ``mkdir(parents=True)``
        call directly instead of routing through here — so a purity check
        on that method keeps finding its own evidence — but reports the
        identical directory set by having ``apply`` query
        :meth:`_missing_parent_dirs` on the same target just beforehand.
        """
        conjured = TransactionApplier._missing_parent_dirs(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        return conjured

    def _recorded_created_dirs(self, recorded: list[str]) -> list[Path]:
        """Resolve a header's ``created_dirs`` to paths inside the project root.

        ``created_dirs`` is data read back off disk, and ``project_root /
        d`` is not a containment operator: an absolute ``d`` discards the
        root entirely and a leading ``..`` walks above it, either of which
        would have rollback ``rmdir`` outside the project — the same
        over-reach this record exists to end, pointed elsewhere. An entry
        whose resolved path is not a strict descendant of the root is
        therefore skipped, not raised on: the rest of the rollback is what
        restores the tree and must still run. Skipping can only leave an
        empty directory behind, which is the direction this code errs in
        everywhere. Order (outermost first) is preserved for
        :meth:`_remove_dirs`.
        """
        root = self._index_store.project_root.resolve()
        inside: list[Path] = []
        for entry in recorded:
            candidate = (root / entry).resolve()
            if candidate != root and candidate.is_relative_to(root):
                inside.append(candidate)
        return inside

    @staticmethod
    def _remove_dirs(directories: list[Path]) -> None:
        """Remove directories recorded by :meth:`_make_parent_dirs`.

        Innermost first, and only while still empty — something else having
        put a file there means the directory is no longer this
        transaction's to remove. Fed two ways: in memory, from
        ``created_dirs`` accumulated during ``apply``, on the mid-apply
        failure path; and read back from ``TransactionHeader.created_dirs``
        via :meth:`_recorded_created_dirs` on ``rollback``. Same list, same
        order, same only-if-empty guard either way.
        """
        for directory in reversed(directories):
            try:
                if directory.is_dir() and not any(directory.iterdir()):
                    directory.rmdir()
            except OSError:  # pragma: no cover — racing writer
                break

    def _unlink_created(self, created: list[str]) -> None:
        """Remove files this transaction already created, on apply failure.

        Safe only because pre-flight (:meth:`_verify_hashes`) already
        proved none of these paths existed before this apply started — this
        is the one asymmetric case in the restore path, and it must never
        run on a path whose absence was not just verified.
        """
        for file_path in created:
            target = self._index_store.project_root / file_path
            if target.exists():
                target.unlink()

    @staticmethod
    def _cleanup_temps(temp_files: dict[str, Path]) -> None:
        """Remove any temp files that were not swapped."""
        for temp_path in temp_files.values():
            if temp_path.exists():
                temp_path.unlink()

    def _reindex_files(
        self, file_paths: list[str]
    ) -> tuple[list[str], list[dict[str, str]]]:
        """Re-index affected files after edits/creates/deletes are applied.

        Returns (reindexed, failed) where ``failed`` contains one
        ``{"file": path, "error": message}`` entry per file whose
        re-index raised. The apply/rollback itself has already succeeded by
        this point (the bytes are on disk), so failures are reported rather
        than raised — but they must not be swallowed, since a stale index
        entry silently corrupts every downstream query and plan.

        A path that no longer exists (a deletion, or a creation undone by
        rollback) has its index entry removed via ``IndexStore.remove``
        instead of being silently skipped — closing the ghost-entry hole a
        bare skip here would otherwise leave for any deleted path.
        """
        adapter = PythonAdapter()
        src_roots = load_src_roots(self._index_store.project_root)
        reindexed: list[str] = []
        failed: list[dict[str, str]] = []

        for file_path in file_paths:
            source_file = self._index_store.project_root / file_path
            if not source_file.exists():
                self._index_store.remove(file_path)
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
