"""Tests for file-lifecycle (create/delete) transactions — Plan D PR1.

No producer emits ``FileCreateEntry``/``FileDeleteEntry`` yet (that is Plan
D PR2's job — flatten_batch etc.). These transactions are hand-built and
round-tripped through save/load/apply/rollback, exactly how
``FileRenameEntry`` was proven before ``rename`` used it.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from pypeeker.cli import main
from pypeeker.models.transaction import (
    EditEntry,
    FileCreateEntry,
    FileDeleteEntry,
    FileRenameEntry,
    TransactionHeader,
    TransactionStatus,
)
from pypeeker.refactor.applier import ApplyError, RollbackError, TransactionApplier
from pypeeker.storage import IndexStore, TransactionStore
from pypeeker.storage.transaction_store import TransactionLoadError


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _tree(project: Path) -> set[str]:
    """Every path under ``project``, files and directories, bar the index."""
    return {
        str(p.relative_to(project))
        for p in project.rglob("*")
        if ".pypeeker" not in p.relative_to(project).parts
    }


def _header(tx_id: str, operation: str = "file-lifecycle") -> TransactionHeader:
    return TransactionHeader(
        tx_id=tx_id,
        symbol_id="n/a",
        old_name="n/a",
        new_name="n/a",
        created_at="2025-01-01T00:00:00+00:00",
        operation=operation,
    )


class TestApplyRollbackRoundTrip:
    """AC: apply/rollback are exact inverses over creates, deletes, edits,
    and a rename in ONE transaction, byte-for-byte including absence/presence.
    """

    def test_create_delete_edit_rename_in_one_transaction(self, project_dir):
        store = IndexStore(project_dir)
        tx_store = TransactionStore(project_dir)

        (project_dir / "edit_me.py").write_text("x = 1\n")
        (project_dir / "delete_me.py").write_text("gone = True\n")
        (project_dir / "old_name.py").write_text("def f(): pass\n")

        edit_hash = IndexStore.compute_file_hash(project_dir / "edit_me.py")
        delete_hash = IndexStore.compute_file_hash(project_dir / "delete_me.py")
        rename_hash = IndexStore.compute_file_hash(project_dir / "old_name.py")

        edits = [
            EditEntry(
                file="edit_me.py", start=4, end=5, old="1", new="2", file_hash=edit_hash
            )
        ]
        create_content = "def created(): pass\n"
        creates = [
            FileCreateEntry(
                path="new_module/created.py",
                content=create_content,
                content_hash=_sha256(create_content),
            )
        ]
        deletes = [
            FileDeleteEntry(
                path="delete_me.py", content="gone = True\n", file_hash=delete_hash
            )
        ]
        file_rename = FileRenameEntry(
            old_path="old_name.py", new_path="new_name.py", file_hash=rename_hash
        )

        header = _header("combo_tx")
        tx_store.save(header, edits, file_rename, creates, deletes)

        # header.version bumped to 2 since creates/deletes are present
        loaded_before = tx_store.load("combo_tx")
        assert loaded_before is not None
        assert loaded_before.header.version == 2

        applier = TransactionApplier(store, tx_store)
        result = applier.apply("combo_tx")

        assert result["status"] == "applied"
        assert result["files_created"] == ["new_module/created.py"]
        assert result["files_deleted"] == ["delete_me.py"]

        assert (project_dir / "edit_me.py").read_text() == "x = 2\n"
        assert not (project_dir / "delete_me.py").exists()
        assert (
            project_dir / "new_module" / "created.py"
        ).read_text() == create_content
        assert not (project_dir / "old_name.py").exists()
        assert (project_dir / "new_name.py").read_text() == "def f(): pass\n"

        # Index hygiene after apply: deleted file gone, created file present.
        assert store.load("delete_me.py") is None
        assert store.load("new_module/created.py") is not None
        assert store.load("old_name.py") is None
        assert store.load("new_name.py") is not None

        rollback_result = applier.rollback("combo_tx")
        assert rollback_result["status"] == "rolled_back"
        assert rollback_result["files_created"] == ["delete_me.py"]
        assert rollback_result["files_deleted"] == ["new_module/created.py"]

        # Byte-exact inverse of the entire transaction.
        assert (project_dir / "edit_me.py").read_text() == "x = 1\n"
        assert (project_dir / "delete_me.py").read_text() == "gone = True\n"
        assert not (project_dir / "new_module" / "created.py").exists()
        assert (project_dir / "old_name.py").read_text() == "def f(): pass\n"
        assert not (project_dir / "new_name.py").exists()

        # Index hygiene after rollback: the inverse of the apply-time state.
        assert store.load("new_module/created.py") is None
        assert store.load("delete_me.py") is not None
        assert store.load("new_name.py") is None
        assert store.load("old_name.py") is not None

        loaded_after = tx_store.load("combo_tx")
        assert loaded_after is not None
        assert loaded_after.header.status == TransactionStatus.ROLLED_BACK


class TestMidApplyCrashConsistency:
    """AC: a mid-apply failure leaves the tree byte-identical, the
    transaction FAILED, and no orphan created file or ``.tmp``.
    """

    def test_failure_during_delete_commit_restores_everything(
        self, project_dir, monkeypatch
    ):
        store = IndexStore(project_dir)
        tx_store = TransactionStore(project_dir)

        (project_dir / "edit_me.py").write_text("x = 1\n")
        (project_dir / "delete_one.py").write_text("one = True\n")
        (project_dir / "delete_two.py").write_text("two = True\n")

        edit_hash = IndexStore.compute_file_hash(project_dir / "edit_me.py")
        d1_hash = IndexStore.compute_file_hash(project_dir / "delete_one.py")
        d2_hash = IndexStore.compute_file_hash(project_dir / "delete_two.py")

        edits = [
            EditEntry(
                file="edit_me.py", start=4, end=5, old="1", new="2", file_hash=edit_hash
            )
        ]
        create_content = "created = True\n"
        creates = [
            FileCreateEntry(
                path="created.py",
                content=create_content,
                content_hash=_sha256(create_content),
            )
        ]
        deletes = [
            FileDeleteEntry(
                path="delete_one.py", content="one = True\n", file_hash=d1_hash
            ),
            FileDeleteEntry(
                path="delete_two.py", content="two = True\n", file_hash=d2_hash
            ),
        ]
        header = _header("mid_fail_tx")
        tx_store.save(header, edits, None, creates, deletes)

        real_unlink = Path.unlink

        def failing_unlink(self, *args, **kwargs):
            if self.name == "delete_two.py":
                raise OSError("simulated I/O failure")
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", failing_unlink)

        applier = TransactionApplier(store, tx_store)
        with pytest.raises(ApplyError, match="rolled back"):
            applier.apply("mid_fail_tx")

        # Tree byte-identical to before apply.
        assert (project_dir / "edit_me.py").read_text() == "x = 1\n"
        assert (project_dir / "delete_one.py").read_text() == "one = True\n"
        assert (project_dir / "delete_two.py").read_text() == "two = True\n"
        assert not (project_dir / "created.py").exists()

        # No orphan .tmp files anywhere in the project.
        assert list(project_dir.glob("**/*.tmp")) == []

        loaded = tx_store.load("mid_fail_tx")
        assert loaded is not None
        assert loaded.header.status == TransactionStatus.FAILED

        # FAILED is terminal.
        with pytest.raises(ApplyError, match="not pending"):
            applier.apply("mid_fail_tx")


class TestPreflightRefusals:
    """AC: pre-flight refuses a creation whose target exists and a deletion
    whose file changed since plan; the transaction stays PENDING and the
    tree is untouched.
    """

    def test_refuses_creation_whose_target_already_exists(self, project_dir):
        store = IndexStore(project_dir)
        tx_store = TransactionStore(project_dir)
        (project_dir / "already_there.py").write_text("existing = True\n")

        creates = [
            FileCreateEntry(
                path="already_there.py",
                content="new = True\n",
                content_hash=_sha256("new = True\n"),
            )
        ]
        header = _header("create_conflict_tx")
        tx_store.save(header, [], None, creates, [])

        applier = TransactionApplier(store, tx_store)
        with pytest.raises(ApplyError, match="already exists"):
            applier.apply("create_conflict_tx")

        assert (
            project_dir / "already_there.py"
        ).read_text() == "existing = True\n"
        loaded = tx_store.load("create_conflict_tx")
        assert loaded is not None
        assert loaded.header.status == TransactionStatus.PENDING

    def test_refuses_deletion_whose_file_changed_since_plan(self, project_dir):
        store = IndexStore(project_dir)
        tx_store = TransactionStore(project_dir)
        (project_dir / "moving_target.py").write_text("original = True\n")
        stale_hash = IndexStore.compute_file_hash(project_dir / "moving_target.py")
        (project_dir / "moving_target.py").write_text("changed = True\n")

        deletes = [
            FileDeleteEntry(
                path="moving_target.py",
                content="original = True\n",
                file_hash=stale_hash,
            )
        ]
        header = _header("delete_conflict_tx")
        tx_store.save(header, [], None, [], deletes)

        applier = TransactionApplier(store, tx_store)
        with pytest.raises(ApplyError, match="modified since plan"):
            applier.apply("delete_conflict_tx")

        assert (
            project_dir / "moving_target.py"
        ).read_text() == "changed = True\n"
        loaded = tx_store.load("delete_conflict_tx")
        assert loaded is not None
        assert loaded.header.status == TransactionStatus.PENDING


class TestRollbackRefusals:
    """AC: rollback refuses a created file edited after apply, and a
    deleted file whose path is now occupied.
    """

    def test_rollback_refuses_when_created_file_edited_after_apply(
        self, project_dir
    ):
        store = IndexStore(project_dir)
        tx_store = TransactionStore(project_dir)
        content = "created = True\n"
        creates = [
            FileCreateEntry(
                path="fresh.py", content=content, content_hash=_sha256(content)
            )
        ]
        header = _header("create_only_tx")
        tx_store.save(header, [], None, creates, [])

        applier = TransactionApplier(store, tx_store)
        applier.apply("create_only_tx")
        assert (project_dir / "fresh.py").read_text() == content

        (project_dir / "fresh.py").write_text("hand edited\n")

        with pytest.raises(RollbackError, match="modified since"):
            applier.rollback("create_only_tx")

        # No partial rollback.
        assert (project_dir / "fresh.py").read_text() == "hand edited\n"
        loaded = tx_store.load("create_only_tx")
        assert loaded is not None
        assert loaded.header.status == TransactionStatus.APPLIED

    def test_rollback_refuses_when_deleted_path_re_occupied(self, project_dir):
        store = IndexStore(project_dir)
        tx_store = TransactionStore(project_dir)
        (project_dir / "to_delete.py").write_text("gone = True\n")
        delete_hash = IndexStore.compute_file_hash(project_dir / "to_delete.py")
        deletes = [
            FileDeleteEntry(
                path="to_delete.py", content="gone = True\n", file_hash=delete_hash
            )
        ]
        header = _header("delete_only_tx")
        tx_store.save(header, [], None, [], deletes)

        applier = TransactionApplier(store, tx_store)
        applier.apply("delete_only_tx")
        assert not (project_dir / "to_delete.py").exists()

        (project_dir / "to_delete.py").write_text("someone else wrote this\n")

        with pytest.raises(RollbackError, match="already exists"):
            applier.rollback("delete_only_tx")

        assert (
            project_dir / "to_delete.py"
        ).read_text() == "someone else wrote this\n"
        loaded = tx_store.load("delete_only_tx")
        assert loaded is not None
        assert loaded.header.status == TransactionStatus.APPLIED


class TestFormatCompatibility:
    """AC: a pre-version transaction file loads and applies byte-identically;
    an unrecognised op or unsupported version refuses cleanly, never a raw
    ``TypeError``.
    """

    def test_versionless_transaction_loads_and_applies_unchanged(
        self, project_dir
    ):
        store = IndexStore(project_dir)
        tx_store = TransactionStore(project_dir)
        (project_dir / "legacy.py").write_text("value = 1\n")
        file_hash = IndexStore.compute_file_hash(project_dir / "legacy.py")

        tx_store.root.mkdir(parents=True, exist_ok=True)
        header_line = {
            "tx_id": "legacy_tx",
            "symbol_id": "legacy:value",
            "old_name": "value",
            "new_name": "value",
            "created_at": "2024-01-01T00:00:00+00:00",
            "operation": "rename",
            "status": "pending",
            "include_file": False,
            "include_exports": False,
            # deliberately no "version" key: this is the pre-PR1 format.
        }
        edit_line = {
            "file": "legacy.py",
            "start": 8,
            "end": 9,
            "old": "1",
            "new": "2",
            "file_hash": file_hash,
            "op": "replace",
        }
        (tx_store.root / "legacy_tx.jsonl").write_text(
            json.dumps(header_line) + "\n" + json.dumps(edit_line) + "\n"
        )

        loaded = tx_store.load("legacy_tx")
        assert loaded is not None
        assert loaded.header.version == 1
        assert loaded.creates == []
        assert loaded.deletes == []
        assert len(loaded.edits) == 1

        applier = TransactionApplier(store, tx_store)
        result = applier.apply("legacy_tx")
        assert result["status"] == "applied"
        assert result["files_created"] == []
        assert result["files_deleted"] == []
        assert (project_dir / "legacy.py").read_text() == "value = 2\n"

    def test_unknown_op_refuses_cleanly_not_type_error(self, project_dir):
        tx_store = TransactionStore(project_dir)
        tx_store.root.mkdir(parents=True, exist_ok=True)
        header_line = {
            "tx_id": "future_op_tx",
            "symbol_id": "n/a",
            "old_name": "n/a",
            "new_name": "n/a",
            "created_at": "2025-01-01T00:00:00+00:00",
            "operation": "rename",
            "status": "pending",
            "include_file": False,
            "include_exports": False,
            "version": 1,
        }
        future_line = {"op": "quantum_teleport_file", "path": "x.py"}
        (tx_store.root / "future_op_tx.jsonl").write_text(
            json.dumps(header_line) + "\n" + json.dumps(future_line) + "\n"
        )

        with pytest.raises(TransactionLoadError) as exc_info:
            tx_store.load("future_op_tx")
        assert exc_info.value.code == "unknown-transaction-op"

    def test_unsupported_version_refuses_cleanly_not_type_error(
        self, project_dir
    ):
        tx_store = TransactionStore(project_dir)
        tx_store.root.mkdir(parents=True, exist_ok=True)
        header_line = {
            "tx_id": "future_version_tx",
            "symbol_id": "n/a",
            "old_name": "n/a",
            "new_name": "n/a",
            "created_at": "2025-01-01T00:00:00+00:00",
            "operation": "rename",
            "status": "pending",
            "include_file": False,
            "include_exports": False,
            "version": 99,
        }
        (tx_store.root / "future_version_tx.jsonl").write_text(
            json.dumps(header_line) + "\n"
        )

        with pytest.raises(TransactionLoadError) as exc_info:
            tx_store.load("future_version_tx")
        assert exc_info.value.code == "unsupported-transaction-version"


class TestFileEntryDropGuard:
    """A partial read-modify-write must not erase a transaction's file entries.

    The tuple-compat shim that made this class necessary is gone (TASK-134):
    ``LoadedTransaction`` is attribute-access only, so the specific hazard of
    a 3-element destructure — read header/edits/rename, save them back,
    silently lose every create and delete line and downgrade the header to
    version 1 — is no longer reachable by that route. It is still reachable
    by hand, which is why ``save``'s version guard is what actually closes
    it and why these tests outlive the shim.
    """

    def test_exposes_header_edits_rename_and_file_entries_by_attribute(
        self, project_dir
    ):
        """Every field a reader could once destructure is still readable.

        The two tuple-compat scenarios this replaces are kept whole: the
        header/edits/rename triple reads back correctly, and creates/deletes
        are reachable — no longer as "the fields the tuple cannot see", but
        as ordinary attributes alongside the rest.
        """
        tx_store = TransactionStore(project_dir)
        header = _header("compat_tx")
        tx_store.save(header, [], None)

        loaded = tx_store.load("compat_tx")
        assert loaded is not None

        assert loaded.header.tx_id == "compat_tx"
        assert loaded.edits == []
        assert loaded.file_rename is None
        assert loaded.creates == []
        assert loaded.deletes == []

        # The shim is retired, not merely unused: a destructure or an index
        # is a runtime error now, so nothing can silently reacquire a view
        # that cannot see creates/deletes.
        with pytest.raises(TypeError):
            _header_, _edits, _rename = loaded
        with pytest.raises(TypeError):
            loaded[0]

    def test_read_modify_write_that_omits_file_entries_refuses(self, project_dir):
        """A load->modify->save round trip must NOT silently drop the
        create/delete lines (and downgrade header.version 2 -> 1).

        The guard defends any partial read-modify-write, not one particular
        access shape: passing header/edits/rename back without both file
        lists is refused whether the caller got them from a destructure (no
        longer possible) or, as here, by reading the attributes it happened
        to care about.
        """
        tx_store = TransactionStore(project_dir)
        content = "born = True\n"
        creates = [
            FileCreateEntry(
                path="born.py", content=content, content_hash=_sha256(content)
            )
        ]
        tx_store.save(_header("lossy_tx"), [], None, creates, [])

        loaded = tx_store.load("lossy_tx")
        assert loaded is not None

        with pytest.raises(ValueError, match="refusing to silently drop"):
            tx_store.save(loaded.header, loaded.edits, loaded.file_rename)

        # The on-disk transaction is untouched: still version 2, still a create.
        reloaded = tx_store.load("lossy_tx")
        assert reloaded is not None
        assert reloaded.header.version == 2
        assert [c.path for c in reloaded.creates] == ["born.py"]

    def test_explicitly_clearing_file_entries_is_allowed(self, project_dir):
        """Passing both lists explicitly is a deliberate act, not a drop."""
        tx_store = TransactionStore(project_dir)
        content = "born = True\n"
        tx_store.save(
            _header("clear_tx"),
            [],
            None,
            [
                FileCreateEntry(
                    path="born.py", content=content, content_hash=_sha256(content)
                )
            ],
            [],
        )
        loaded = tx_store.load("clear_tx")
        assert loaded is not None

        tx_store.save(loaded.header, loaded.edits, loaded.file_rename, [], [])
        reloaded = tx_store.load("clear_tx")
        assert reloaded is not None
        assert reloaded.creates == []
        assert reloaded.header.version == 1


class TestConflictingEntriesRefused:
    """Pre-flight validates entries against EACH OTHER, not only against the
    tree. Two entries naming one path can each hash-match and still leave an
    APPLIED transaction with no inverse, so they are refused up front.
    """

    def _assert_refused_and_pending(self, applier, tx_store, tx_id):
        with pytest.raises(ApplyError, match="Conflicting entries"):
            applier.apply(tx_id)
        loaded = tx_store.load(tx_id)
        assert loaded is not None
        assert loaded.header.status == TransactionStatus.PENDING

    def test_edit_and_delete_of_same_path_refuses(self, project_dir):
        """Otherwise: the edit commits, the delete unlinks it, apply reports
        'applied', and rollback raises 'File not found' forever.
        """
        store = IndexStore(project_dir)
        tx_store = TransactionStore(project_dir)
        (project_dir / "both.py").write_text("x = 1\n")
        file_hash = IndexStore.compute_file_hash(project_dir / "both.py")

        tx_store.save(
            _header("edit_delete_tx"),
            [
                EditEntry(
                    file="both.py", start=4, end=5, old="1", new="2",
                    file_hash=file_hash,
                )
            ],
            None,
            [],
            [
                FileDeleteEntry(
                    path="both.py", content="x = 1\n", file_hash=file_hash
                )
            ],
        )

        applier = TransactionApplier(store, tx_store)
        self._assert_refused_and_pending(applier, tx_store, "edit_delete_tx")
        assert (project_dir / "both.py").read_text() == "x = 1\n"

    def test_create_and_rename_onto_same_path_refuses(self, project_dir):
        """Otherwise: the creation commits and the rename silently overwrites
        it; the created content is unrecoverable and rollback refuses forever.
        """
        store = IndexStore(project_dir)
        tx_store = TransactionStore(project_dir)
        (project_dir / "x.py").write_text("moved = True\n")
        rename_hash = IndexStore.compute_file_hash(project_dir / "x.py")
        content = "created = True\n"

        tx_store.save(
            _header("create_rename_tx"),
            [],
            FileRenameEntry(
                old_path="x.py", new_path="p.py", file_hash=rename_hash
            ),
            [
                FileCreateEntry(
                    path="p.py", content=content, content_hash=_sha256(content)
                )
            ],
            [],
        )

        applier = TransactionApplier(store, tx_store)
        self._assert_refused_and_pending(applier, tx_store, "create_rename_tx")
        assert (project_dir / "x.py").read_text() == "moved = True\n"
        assert not (project_dir / "p.py").exists()

    def test_delete_and_rename_onto_same_path_refuses(self, project_dir):
        """Otherwise: apply succeeds and rollback refuses forever, because
        restoring the deleted file finds its path occupied by the rename.
        """
        store = IndexStore(project_dir)
        tx_store = TransactionStore(project_dir)
        (project_dir / "dd.py").write_text("doomed = True\n")
        (project_dir / "x.py").write_text("moved = True\n")
        dd_hash = IndexStore.compute_file_hash(project_dir / "dd.py")
        x_hash = IndexStore.compute_file_hash(project_dir / "x.py")

        tx_store.save(
            _header("delete_rename_tx"),
            [],
            FileRenameEntry(old_path="x.py", new_path="dd.py", file_hash=x_hash),
            [],
            [
                FileDeleteEntry(
                    path="dd.py", content="doomed = True\n", file_hash=dd_hash
                )
            ],
        )

        applier = TransactionApplier(store, tx_store)
        self._assert_refused_and_pending(applier, tx_store, "delete_rename_tx")
        assert (project_dir / "dd.py").read_text() == "doomed = True\n"
        assert (project_dir / "x.py").read_text() == "moved = True\n"

    def test_two_creations_of_same_path_refuse(self, project_dir):
        store = IndexStore(project_dir)
        tx_store = TransactionStore(project_dir)
        first, second = "first = True\n", "second = True\n"

        tx_store.save(
            _header("double_create_tx"),
            [],
            None,
            [
                FileCreateEntry(
                    path="dup.py", content=first, content_hash=_sha256(first)
                ),
                FileCreateEntry(
                    path="dup.py", content=second, content_hash=_sha256(second)
                ),
            ],
            [],
        )

        applier = TransactionApplier(store, tx_store)
        self._assert_refused_and_pending(applier, tx_store, "double_create_tx")
        assert not (project_dir / "dup.py").exists()

    def test_editing_a_file_and_renaming_it_is_still_allowed(self, project_dir):
        """The one legitimate path sharing — the module-rename shape — must
        keep working; the conflict check must not over-refuse it.
        """
        store = IndexStore(project_dir)
        tx_store = TransactionStore(project_dir)
        (project_dir / "user.py").write_text("name = 'user'\n")
        file_hash = IndexStore.compute_file_hash(project_dir / "user.py")

        tx_store.save(
            _header("edit_and_rename_tx"),
            [
                EditEntry(
                    file="user.py", start=7, end=13, old="'user'", new="'acct'",
                    file_hash=file_hash,
                )
            ],
            FileRenameEntry(
                old_path="user.py", new_path="account.py", file_hash=file_hash
            ),
        )

        applier = TransactionApplier(store, tx_store)
        result = applier.apply("edit_and_rename_tx")
        assert result["status"] == "applied"
        assert (project_dir / "account.py").read_text() == "name = 'acct'\n"

        applier.rollback("edit_and_rename_tx")
        assert (project_dir / "user.py").read_text() == "name = 'user'\n"
        assert not (project_dir / "account.py").exists()


class TestPinnedContentIsVerified:
    """An entry's ``content`` is the only pre-image rollback has. If it does
    not hash to the hash the entry pins, applying it corrupts the inverse
    silently — so pre-flight refuses instead.
    """

    def test_delete_whose_content_does_not_match_file_hash_refuses(
        self, project_dir
    ):
        """The realistic producer bug: ``path.read_text()`` decodes CRLF to
        LF while ``file_hash`` is over the raw bytes. Both hashes are
        'correct' for their own input, apply passes, and rollback silently
        restores different bytes.
        """
        store = IndexStore(project_dir)
        tx_store = TransactionStore(project_dir)
        raw = b"def gone():\r\n    return 1\r\n"
        (project_dir / "crlf.py").write_bytes(raw)
        file_hash = IndexStore.compute_file_hash(project_dir / "crlf.py")

        # Universal-newline decode: '\r\n' -> '\n'. Hashes to something else.
        decoded = (project_dir / "crlf.py").read_text()
        assert _sha256(decoded) != file_hash

        tx_store.save(
            _header("crlf_tx"),
            [],
            None,
            [],
            [
                FileDeleteEntry(
                    path="crlf.py", content=decoded, file_hash=file_hash
                )
            ],
        )

        applier = TransactionApplier(store, tx_store)
        with pytest.raises(ApplyError, match="Malformed entry"):
            applier.apply("crlf_tx")

        assert (project_dir / "crlf.py").read_bytes() == raw
        loaded = tx_store.load("crlf_tx")
        assert loaded is not None
        assert loaded.header.status == TransactionStatus.PENDING

    def test_create_whose_content_hash_is_wrong_refuses(self, project_dir):
        """Otherwise rollback is permanently impossible, with a misleading
        'has been modified since applied' message.
        """
        store = IndexStore(project_dir)
        tx_store = TransactionStore(project_dir)

        tx_store.save(
            _header("bad_create_hash_tx"),
            [],
            None,
            [
                FileCreateEntry(
                    path="fresh.py",
                    content="real = True\n",
                    content_hash=_sha256("something else\n"),
                )
            ],
            [],
        )

        applier = TransactionApplier(store, tx_store)
        with pytest.raises(ApplyError, match="Malformed entry"):
            applier.apply("bad_create_hash_tx")

        assert not (project_dir / "fresh.py").exists()
        loaded = tx_store.load("bad_create_hash_tx")
        assert loaded is not None
        assert loaded.header.status == TransactionStatus.PENDING


class TestDirectoryHygiene:
    """Directories conjured for a creation are part of that creation's
    mutation. A FAILED apply and a completed rollback must both remove them
    — under PEP 420 a leftover empty directory is an importable namespace
    package, so the tree would not be byte-identical (nor semantically so).

    Rollback removes exactly the recorded set (``TransactionHeader.
    created_dirs``, stamped at apply time) — no more, no less: a directory
    that pre-existed the transaction, even empty, is not in that set and
    must survive rollback.
    """

    def test_failed_apply_leaves_no_conjured_directory(
        self, project_dir, monkeypatch
    ):
        store = IndexStore(project_dir)
        tx_store = TransactionStore(project_dir)
        (project_dir / "delete_me.py").write_text("gone = True\n")
        delete_hash = IndexStore.compute_file_hash(project_dir / "delete_me.py")

        before = sorted(p.name for p in project_dir.iterdir())

        content = "created = True\n"
        tx_store.save(
            _header("dir_fail_tx"),
            [],
            None,
            [
                FileCreateEntry(
                    path="pkg/sub/new.py",
                    content=content,
                    content_hash=_sha256(content),
                )
            ],
            [
                FileDeleteEntry(
                    path="delete_me.py",
                    content="gone = True\n",
                    file_hash=delete_hash,
                )
            ],
        )

        real_unlink = Path.unlink

        def failing_unlink(self, *args, **kwargs):
            if self.name == "delete_me.py":
                raise OSError("simulated I/O failure")
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", failing_unlink)

        applier = TransactionApplier(store, tx_store)
        with pytest.raises(ApplyError, match="rolled back"):
            applier.apply("dir_fail_tx")

        assert not (project_dir / "pkg").exists()
        assert sorted(p.name for p in project_dir.iterdir()) == before
        assert list(project_dir.glob("**/*.tmp")) == []

    def test_rollback_removes_directories_the_creation_conjured(
        self, project_dir
    ):
        store = IndexStore(project_dir)
        tx_store = TransactionStore(project_dir)
        before = sorted(p.name for p in project_dir.iterdir())

        content = "created = True\n"
        tx_store.save(
            _header("dir_roundtrip_tx"),
            [],
            None,
            [
                FileCreateEntry(
                    path="brandnew/sub/new.py",
                    content=content,
                    content_hash=_sha256(content),
                )
            ],
            [],
        )

        applier = TransactionApplier(store, tx_store)
        applier.apply("dir_roundtrip_tx")
        assert (project_dir / "brandnew" / "sub" / "new.py").exists()

        applier.rollback("dir_roundtrip_tx")
        assert not (project_dir / "brandnew" / "sub").exists()
        assert not (project_dir / "brandnew").exists()
        assert sorted(p.name for p in project_dir.iterdir()) == before

    def test_rollback_keeps_a_directory_that_still_holds_something(
        self, project_dir
    ):
        """Pruning stops at the first non-empty directory: a pre-existing
        package that merely gained a file must survive the rollback.
        """
        store = IndexStore(project_dir)
        tx_store = TransactionStore(project_dir)
        (project_dir / "pkg").mkdir()
        (project_dir / "pkg" / "keep.py").write_text("keep = True\n")

        content = "created = True\n"
        tx_store.save(
            _header("dir_keep_tx"),
            [],
            None,
            [
                FileCreateEntry(
                    path="pkg/new.py",
                    content=content,
                    content_hash=_sha256(content),
                )
            ],
            [],
        )

        applier = TransactionApplier(store, tx_store)
        applier.apply("dir_keep_tx")
        applier.rollback("dir_keep_tx")

        assert not (project_dir / "pkg" / "new.py").exists()
        assert (project_dir / "pkg" / "keep.py").read_text() == "keep = True\n"

    def test_rollback_spares_a_directory_that_pre_existed_the_transaction(
        self, project_dir
    ):
        """AC #1: a pre-existing EMPTY directory is not conjured, so it is
        not in ``created_dirs`` and rollback must leave it in place — the
        opposite of the walk-up-and-guess pruning this replaces, which could
        not tell a pre-existing empty directory from a conjured one.
        """
        store = IndexStore(project_dir)
        tx_store = TransactionStore(project_dir)
        (project_dir / "pkg").mkdir()
        (project_dir / "pkg" / "sub").mkdir()

        content = "created = True\n"
        tx_store.save(
            _header("dir_preexisting_tx"),
            [],
            None,
            [
                FileCreateEntry(
                    path="pkg/sub/new.py",
                    content=content,
                    content_hash=_sha256(content),
                )
            ],
            [],
        )

        applier = TransactionApplier(store, tx_store)
        applier.apply("dir_preexisting_tx")

        loaded = tx_store.load("dir_preexisting_tx")
        assert loaded is not None
        assert loaded.header.created_dirs == []

        applier.rollback("dir_preexisting_tx")

        assert not (project_dir / "pkg" / "sub" / "new.py").exists()
        assert (project_dir / "pkg" / "sub").is_dir()
        assert list((project_dir / "pkg" / "sub").iterdir()) == []
        assert (project_dir / "pkg").is_dir()

    def test_repeated_apply_rollback_cycles_accumulate_no_directories(
        self, project_dir
    ):
        """AC #2: plan/apply/rollback, repeated, leaves the tree unchanged
        each time — the conjured directories are removed exactly, not
        merely "eventually", so nothing accumulates across cycles.
        """
        store = IndexStore(project_dir)
        tx_store = TransactionStore(project_dir)
        before = _tree(project_dir)

        content = "created = True\n"
        for n in range(3):
            tx_id = f"dir_cycle_tx_{n}"
            tx_store.save(
                _header(tx_id),
                [],
                None,
                [
                    FileCreateEntry(
                        path="brandnew/sub/new.py",
                        content=content,
                        content_hash=_sha256(content),
                    )
                ],
                [],
            )

            applier = TransactionApplier(store, tx_store)
            applier.apply(tx_id)

            loaded = tx_store.load(tx_id)
            assert loaded is not None
            assert loaded.header.created_dirs == ["brandnew", "brandnew/sub"]

            applier.rollback(tx_id)
            assert _tree(project_dir) == before

    def test_a_record_without_created_dirs_removes_no_directory(
        self, project_dir
    ):
        """Legacy fallback: a header written before ``created_dirs`` existed
        has no directory evidence, so rollback removes nothing.

        No evidence licenses no removal. The price is a PEP 420-importable
        empty directory left behind, which is the conservative direction
        (residue, never destruction of a directory the transaction did not
        own) and can only ever affect a transaction that was APPLIED by a
        build predating this field — no new transaction can ever lack the
        key.
        """
        store = IndexStore(project_dir)
        tx_store = TransactionStore(project_dir)

        content = "created = True\n"
        tx_store.save(
            _header("dir_legacy_tx"),
            [],
            None,
            [
                FileCreateEntry(
                    path="brandnew/sub/new.py",
                    content=content,
                    content_hash=_sha256(content),
                )
            ],
            [],
        )

        applier = TransactionApplier(store, tx_store)
        applier.apply("dir_legacy_tx")

        tx_path = tx_store.root / "dir_legacy_tx.jsonl"
        lines = tx_path.read_text().strip().split("\n")
        header_data = json.loads(lines[0])
        assert header_data["created_dirs"] == ["brandnew", "brandnew/sub"]
        del header_data["created_dirs"]
        lines[0] = json.dumps(header_data)
        tx_path.write_text("\n".join(lines) + "\n")

        loaded = tx_store.load("dir_legacy_tx")
        assert loaded is not None
        assert loaded.header.created_dirs is None

        applier.rollback("dir_legacy_tx")

        assert not (project_dir / "brandnew" / "sub" / "new.py").exists()
        assert (project_dir / "brandnew" / "sub").is_dir()


class TestRenameDirectoryHygiene:
    """A rename's target directory is conjured by the same apply and is just
    as much part of the mutation as a creation's — TASK-140, the under-reach
    twin of TASK-137's over-reach: directories a rename conjured used to go
    unrecorded, leaking on both a mid-apply failure and a completed
    rollback while ``_apply_file_rename``'s own bare ``mkdir`` did the work
    off the books.
    """

    def test_rollback_removes_directories_the_rename_conjured(self, project_dir):
        """AC #1: a rename into a not-yet-existing directory records that
        directory on the header, and rollback removes it along with
        reversing the rename.
        """
        store = IndexStore(project_dir)
        tx_store = TransactionStore(project_dir)
        (project_dir / "old_name.py").write_text("def f(): pass\n")
        before = _tree(project_dir)
        rename_hash = IndexStore.compute_file_hash(project_dir / "old_name.py")

        tx_store.save(
            _header("rename_dir_tx"),
            [],
            FileRenameEntry(
                old_path="old_name.py",
                new_path="brandnew/sub/new_name.py",
                file_hash=rename_hash,
            ),
            [],
            [],
        )

        applier = TransactionApplier(store, tx_store)
        applier.apply("rename_dir_tx")

        loaded = tx_store.load("rename_dir_tx")
        assert loaded is not None
        assert loaded.header.created_dirs == ["brandnew", "brandnew/sub"]

        applier.rollback("rename_dir_tx")

        assert (project_dir / "old_name.py").exists()
        assert not (project_dir / "brandnew").exists()
        assert _tree(project_dir) == before

    def test_failed_apply_leaves_no_rename_conjured_directory(
        self, project_dir, monkeypatch
    ):
        """AC #2: a mid-apply failure after the rename staged a new
        directory removes that directory, just like a creation-conjured one.
        """
        store = IndexStore(project_dir)
        tx_store = TransactionStore(project_dir)
        (project_dir / "old_name.py").write_text("def f(): pass\n")
        before = _tree(project_dir)
        rename_hash = IndexStore.compute_file_hash(project_dir / "old_name.py")

        tx_store.save(
            _header("rename_fail_tx"),
            [],
            FileRenameEntry(
                old_path="old_name.py",
                new_path="brandnew/sub/new_name.py",
                file_hash=rename_hash,
            ),
            [],
            [],
        )

        def failing_rename(self, target):
            raise OSError("simulated I/O failure")

        monkeypatch.setattr(Path, "rename", failing_rename)

        applier = TransactionApplier(store, tx_store)
        with pytest.raises(ApplyError, match="rolled back"):
            applier.apply("rename_fail_tx")

        assert not (project_dir / "brandnew").exists()
        assert _tree(project_dir) == before
        loaded = tx_store.load("rename_fail_tx")
        assert loaded is not None
        assert loaded.header.status == TransactionStatus.FAILED

    def test_rollback_spares_a_directory_the_rename_did_not_create(
        self, project_dir
    ):
        """The TASK-137 guarantee extended to renames: a rename into an
        already-existing empty directory records nothing, so rollback must
        leave that directory in place.
        """
        store = IndexStore(project_dir)
        tx_store = TransactionStore(project_dir)
        (project_dir / "old_name.py").write_text("def f(): pass\n")
        (project_dir / "pkg").mkdir()
        (project_dir / "pkg" / "sub").mkdir()
        rename_hash = IndexStore.compute_file_hash(project_dir / "old_name.py")

        tx_store.save(
            _header("rename_pre_tx"),
            [],
            FileRenameEntry(
                old_path="old_name.py",
                new_path="pkg/sub/new_name.py",
                file_hash=rename_hash,
            ),
            [],
            [],
        )

        applier = TransactionApplier(store, tx_store)
        applier.apply("rename_pre_tx")

        loaded = tx_store.load("rename_pre_tx")
        assert loaded is not None
        assert loaded.header.created_dirs == []

        applier.rollback("rename_pre_tx")

        assert (project_dir / "pkg" / "sub").is_dir()
        assert list((project_dir / "pkg" / "sub").iterdir()) == []

    def test_a_creation_and_a_rename_sharing_ancestors_record_them_once(
        self, project_dir
    ):
        """A create and a rename that conjure the same ancestor directories
        in one transaction record each directory once, not twice: whichever
        entry stages first (creates stage before the rename's commit-phase
        mkdir) conjures it, and the other sees it already there.
        """
        store = IndexStore(project_dir)
        tx_store = TransactionStore(project_dir)
        (project_dir / "old_name.py").write_text("def f(): pass\n")
        before = _tree(project_dir)
        rename_hash = IndexStore.compute_file_hash(project_dir / "old_name.py")
        content = "created = True\n"

        tx_store.save(
            _header("overlap_tx"),
            [],
            FileRenameEntry(
                old_path="old_name.py",
                new_path="pkg/sub/renamed.py",
                file_hash=rename_hash,
            ),
            [
                FileCreateEntry(
                    path="pkg/sub/new.py",
                    content=content,
                    content_hash=_sha256(content),
                )
            ],
            [],
        )

        applier = TransactionApplier(store, tx_store)
        applier.apply("overlap_tx")

        loaded = tx_store.load("overlap_tx")
        assert loaded is not None
        assert loaded.header.created_dirs == ["pkg", "pkg/sub"]

        applier.rollback("overlap_tx")
        assert _tree(project_dir) == before


class TestDirectoryRecordStaysInsideTheProject:
    """``created_dirs`` is a path list written at apply time and read back,
    in a later process, by rollback. Both ends have to keep it — and the
    ``rmdir`` it authorises — inside the project root. An entry path that
    escapes the root must not (a) crash the apply after its commit, nor
    (b) point rollback's removal at a directory outside the project, which
    would be the same over-reach this record exists to end, redirected.
    """

    def test_a_creation_path_escaping_the_root_fails_the_whole_apply(
        self, project_dir
    ):
        """An absolute creation path cannot be expressed relative to the
        project root, so recording it raises. That raise happens during
        staging, inside the failure handler's reach: the transaction ends
        FAILED with an ApplyError (which the CLI renders as its error
        envelope), and the directory staging conjured outside the root is
        taken back out. A raise after the commit instead would leave a
        mutated tree under a still-PENDING header — no rollback, no
        re-apply, no inverse.
        """
        store = IndexStore(project_dir)
        tx_store = TransactionStore(project_dir)
        escaped_dir = project_dir.parent / "pk_escaped_absolute"
        assert not escaped_dir.exists()

        content = "escaped = True\n"
        tx_store.save(
            _header("dir_escape_abs_tx"),
            [],
            None,
            [
                FileCreateEntry(
                    path=str(escaped_dir / "new.py"),
                    content=content,
                    content_hash=_sha256(content),
                )
            ],
            [],
        )

        applier = TransactionApplier(store, tx_store)
        with pytest.raises(ApplyError) as excinfo:
            applier.apply("dir_escape_abs_tx")
        assert "Apply failed, rolled back" in str(excinfo.value)

        loaded = tx_store.load("dir_escape_abs_tx")
        assert loaded is not None
        assert loaded.header.status == TransactionStatus.FAILED
        assert not (escaped_dir / "new.py").exists()
        assert not escaped_dir.exists()

    def test_rollback_spares_a_recorded_directory_that_resolves_outside_the_root(
        self, project_dir
    ):
        """A ``..`` creation path records a ``..`` directory, and
        ``project_root / "../x"`` walks above the root. Rollback removes
        the created file but must not ``rmdir`` there — outside the
        project is not this transaction's to clean, even when empty.
        """
        store = IndexStore(project_dir)
        tx_store = TransactionStore(project_dir)
        outside = project_dir.parent / "pk_outside_relative"
        assert not outside.exists()

        content = "outside = True\n"
        tx_store.save(
            _header("dir_escape_rel_tx"),
            [],
            None,
            [
                FileCreateEntry(
                    path="../pk_outside_relative/new.py",
                    content=content,
                    content_hash=_sha256(content),
                )
            ],
            [],
        )

        applier = TransactionApplier(store, tx_store)
        applier.apply("dir_escape_rel_tx")

        loaded = tx_store.load("dir_escape_rel_tx")
        assert loaded is not None
        assert loaded.header.created_dirs == ["../pk_outside_relative"]
        assert (outside / "new.py").read_text() == content

        applier.rollback("dir_escape_rel_tx")

        assert not (outside / "new.py").exists()
        assert outside.is_dir()
        assert list(outside.iterdir()) == []

    def test_rollback_ignores_an_absolute_recorded_directory(self, project_dir):
        """A header carrying an absolute ``created_dirs`` entry — corrupt,
        hand-edited, or written by some future producer — must not steer
        rollback outside the project. ``project_root / "/victim"`` is
        ``/victim``: containment is checked, not assumed.
        """
        store = IndexStore(project_dir)
        tx_store = TransactionStore(project_dir)
        victim = project_dir.parent / "pk_victim_absolute"
        victim.mkdir()

        content = "created = True\n"
        tx_store.save(
            _header("dir_absolute_record_tx"),
            [],
            None,
            [
                FileCreateEntry(
                    path="brandnew/new.py",
                    content=content,
                    content_hash=_sha256(content),
                )
            ],
            [],
        )

        applier = TransactionApplier(store, tx_store)
        applier.apply("dir_absolute_record_tx")

        tx_path = tx_store.root / "dir_absolute_record_tx.jsonl"
        lines = tx_path.read_text().strip().split("\n")
        header_data = json.loads(lines[0])
        header_data["created_dirs"] = [str(victim)]
        lines[0] = json.dumps(header_data)
        tx_path.write_text("\n".join(lines) + "\n")

        applier.rollback("dir_absolute_record_tx")

        assert victim.is_dir()
        assert not (project_dir / "brandnew" / "new.py").exists()

    def test_rollback_ignores_a_recorded_entry_naming_the_project_root(
        self, project_dir
    ):
        """``""`` and ``"."`` resolve to the project root itself. Rollback
        removes directories *under* the root; the root is never one of
        them, and an entry claiming otherwise is dropped.
        """
        store = IndexStore(project_dir)
        tx_store = TransactionStore(project_dir)

        content = "created = True\n"
        tx_store.save(
            _header("dir_root_record_tx"),
            [],
            None,
            [
                FileCreateEntry(
                    path="brandnew/new.py",
                    content=content,
                    content_hash=_sha256(content),
                )
            ],
            [],
        )

        applier = TransactionApplier(store, tx_store)
        applier.apply("dir_root_record_tx")

        tx_path = tx_store.root / "dir_root_record_tx.jsonl"
        lines = tx_path.read_text().strip().split("\n")
        header_data = json.loads(lines[0])
        header_data["created_dirs"] = [".", "brandnew"]
        lines[0] = json.dumps(header_data)
        tx_path.write_text("\n".join(lines) + "\n")

        applier.rollback("dir_root_record_tx")

        assert project_dir.is_dir()
        assert not (project_dir / "brandnew").exists()

    def test_a_rename_target_escaping_the_root_fails_the_whole_apply(
        self, project_dir
    ):
        """The rename twin of the creation case above: a rename target that
        cannot be expressed relative to the project root must not silently
        move the file outside the project and mark the transaction applied.
        Recording it raises during the commit phase, inside the failure
        handler's reach, so the whole apply is rolled back and FAILED.
        """
        store = IndexStore(project_dir)
        tx_store = TransactionStore(project_dir)
        (project_dir / "old_name.py").write_text("def f(): pass\n")
        rename_hash = IndexStore.compute_file_hash(project_dir / "old_name.py")
        escaped_dir = project_dir.parent / "pk_escaped_rename"
        assert not escaped_dir.exists()

        tx_store.save(
            _header("rename_escape_tx"),
            [],
            FileRenameEntry(
                old_path="old_name.py",
                new_path=str(escaped_dir / "escaped.py"),
                file_hash=rename_hash,
            ),
            [],
            [],
        )

        applier = TransactionApplier(store, tx_store)
        with pytest.raises(ApplyError, match="Apply failed, rolled back"):
            applier.apply("rename_escape_tx")

        assert (project_dir / "old_name.py").exists()
        assert not escaped_dir.exists()
        loaded = tx_store.load("rename_escape_tx")
        assert loaded is not None
        assert loaded.header.status == TransactionStatus.FAILED


class TestVersioning:
    """header.version is written as 2 only when creates/deletes are present."""

    def test_version_stays_1_with_only_edits_and_rename(self, project_dir):
        tx_store = TransactionStore(project_dir)
        header = _header("v1_tx")
        edits = [
            EditEntry(file="a.py", start=0, end=1, old="a", new="b", file_hash="h")
        ]
        rename = FileRenameEntry(old_path="a.py", new_path="b.py", file_hash="h")
        tx_store.save(header, edits, rename)

        loaded = tx_store.load("v1_tx")
        assert loaded is not None
        assert loaded.header.version == 1

    def test_version_becomes_2_when_creates_present(self, project_dir):
        tx_store = TransactionStore(project_dir)
        header = _header("v2_create_tx")
        creates = [
            FileCreateEntry(path="new.py", content="x\n", content_hash=_sha256("x\n"))
        ]
        tx_store.save(header, [], None, creates, [])

        loaded = tx_store.load("v2_create_tx")
        assert loaded is not None
        assert loaded.header.version == 2

    def test_version_becomes_2_when_deletes_present(self, project_dir):
        tx_store = TransactionStore(project_dir)
        header = _header("v2_delete_tx")
        deletes = [FileDeleteEntry(path="gone.py", content="x\n", file_hash="h")]
        tx_store.save(header, [], None, [], deletes)

        loaded = tx_store.load("v2_delete_tx")
        assert loaded is not None
        assert loaded.header.version == 2


def _make_project(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create a project directory with source files and pyproject.toml."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
    (tmp_path / ".pypeeker" / "index").mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return tmp_path


class TestTransactionsCliRendersFileLifecycleEntries:
    """AC: ``transactions show``/``list`` render the new entries with the
    existing keys unchanged.
    """

    def test_show_renders_creates_and_deletes_alongside_existing_keys(
        self, tmp_path
    ):
        project = _make_project(
            tmp_path, {"kept.py": "keep = True\n", "gone.py": "gone = True\n"}
        )
        runner = CliRunner()
        os.chdir(project)

        gone_hash = IndexStore.compute_file_hash(project / "gone.py")
        tx_store = TransactionStore(project)
        header = _header("lifecycle_tx")
        born_content = "born = True\n"
        creates = [
            FileCreateEntry(
                path="born.py", content=born_content, content_hash=_sha256(born_content)
            )
        ]
        deletes = [
            FileDeleteEntry(path="gone.py", content="gone = True\n", file_hash=gone_hash)
        ]
        tx_store.save(header, [], None, creates, deletes)

        result = runner.invoke(
            main, ["transactions", "show", "lifecycle_tx"], catch_exceptions=False
        )
        assert result.exit_code == 0
        output = json.loads(result.output)
        # Existing keys unchanged, plus the new additive ones.
        assert set(output.keys()) == {
            "header",
            "edits",
            "file_rename",
            "creates",
            "deletes",
        }
        assert output["edits"] == []
        assert output["file_rename"] is None
        assert output["header"]["version"] == 2
        assert output["creates"] == [
            {
                "path": "born.py",
                "content": born_content,
                "content_hash": _sha256(born_content),
                "op": "create_file",
            }
        ]
        assert output["deletes"] == [
            {
                "path": "gone.py",
                "content": "gone = True\n",
                "file_hash": gone_hash,
                "op": "delete_file",
            }
        ]

    def test_list_counts_creates_and_deletes_in_edit_count(self, tmp_path):
        project = _make_project(
            tmp_path, {"kept.py": "keep = True\n", "gone.py": "gone = True\n"}
        )
        runner = CliRunner()
        os.chdir(project)

        gone_hash = IndexStore.compute_file_hash(project / "gone.py")
        tx_store = TransactionStore(project)
        header = _header("lifecycle_list_tx")
        creates = [
            FileCreateEntry(path="born.py", content="born = True\n", content_hash=_sha256("born = True\n"))
        ]
        deletes = [
            FileDeleteEntry(path="gone.py", content="gone = True\n", file_hash=gone_hash)
        ]
        tx_store.save(header, [], None, creates, deletes)

        result = runner.invoke(
            main, ["transactions", "list"], catch_exceptions=False
        )
        assert result.exit_code == 0
        listed = json.loads(result.output)
        assert len(listed) == 1
        entry = listed[0]
        # Existing keys unchanged.
        assert set(entry.keys()) == {
            "tx_id",
            "operation",
            "status",
            "created_at",
            "edit_count",
            "files_affected",
        }
        assert entry["edit_count"] == 2
        assert sorted(entry["files_affected"]) == ["born.py", "gone.py"]


def _write_raw_transaction(project: Path, tx_id: str, lines: list[dict]) -> None:
    """Hand-write a transaction file, bypassing TransactionStore.save."""
    root = TransactionStore(project).root
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{tx_id}.jsonl").write_text(
        "".join(json.dumps(line) + "\n" for line in lines)
    )


def _raw_header(tx_id: str, **overrides) -> dict:
    header = {
        "tx_id": tx_id,
        "symbol_id": "n/a",
        "old_name": "n/a",
        "new_name": "n/a",
        "created_at": "2025-01-01T00:00:00+00:00",
        "operation": "rename",
        "status": "pending",
        "include_file": False,
        "include_exports": False,
        "version": 1,
    }
    header.update(overrides)
    return header


class TestUnreadableTransactionAtTheCliBoundary:
    """The version gate exists so a forward-format transaction refuses
    cleanly. ``TransactionLoadError`` carries a stable ``code`` precisely so
    the CLI can surface it — a traceback with empty stdout is as unparseable
    to an LLM-driven consumer as the raw ``TypeError`` it replaced, and the
    tool's contract is that output is JSON.
    """

    @pytest.fixture
    def project(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, {"kept.py": "keep = True\n"})
        monkeypatch.chdir(project)
        return project

    def test_list_degrades_the_bad_entry_instead_of_dying(self, project):
        """A single unreadable file must not make every OTHER transaction
        invisible — before the version gate existed, a forward header simply
        loaded, so aborting the whole listing would be a pure regression.
        """
        kept_hash = IndexStore.compute_file_hash(project / "kept.py")
        tx_store = TransactionStore(project)
        tx_store.save(
            _header("good_tx", operation="rename"),
            [
                EditEntry(
                    file="kept.py", start=0, end=4, old="keep", new="held",
                    file_hash=kept_hash,
                )
            ],
        )
        _write_raw_transaction(
            project, "badver_tx", [_raw_header("badver_tx", version=99)]
        )
        _write_raw_transaction(
            project,
            "badop_tx",
            [_raw_header("badop_tx"), {"op": "move_file", "path": "x.py"}],
        )

        result = CliRunner().invoke(main, ["transactions", "list"])
        assert result.exit_code == 0, result.output
        listed = {entry["tx_id"]: entry for entry in json.loads(result.output)}
        assert set(listed) == {"good_tx", "badver_tx", "badop_tx"}

        # The readable one is listed normally.
        assert listed["good_tx"]["status"] == "pending"
        assert listed["good_tx"]["edit_count"] == 1

        # The unreadable ones degrade, machine-readably.
        assert listed["badver_tx"]["status"] == "unreadable"
        assert listed["badver_tx"]["code"] == "unsupported-transaction-version"
        assert listed["badop_tx"]["status"] == "unreadable"
        assert listed["badop_tx"]["code"] == "unknown-transaction-op"

    @pytest.mark.parametrize(
        ("lines", "expected_code"),
        [
            ([_raw_header("bad_tx", version=99)], "unsupported-transaction-version"),
            (
                [_raw_header("bad_tx"), {"op": "move_file", "path": "x.py"}],
                "unknown-transaction-op",
            ),
            (
                [_raw_header("bad_tx"), {"path": "x.py"}],  # no "op" key at all
                "unknown-transaction-op",
            ),
        ],
        ids=["future-version", "future-op", "missing-op"],
    )
    @pytest.mark.parametrize(
        "command",
        [
            ["transactions", "show", "bad_tx"],
            ["transactions", "cancel", "bad_tx"],
            ["apply", "bad_tx"],
            ["rollback", "bad_tx"],
        ],
        ids=["show", "cancel", "apply", "rollback"],
    )
    def test_every_command_emits_the_standard_error_envelope(
        self, project, command, lines, expected_code
    ):
        _write_raw_transaction(project, "bad_tx", lines)

        result = CliRunner().invoke(main, command)
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload["code"] == expected_code
        assert payload["error"]
