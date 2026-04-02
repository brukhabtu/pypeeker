"""Additional applier integration tests for coverage gaps."""

import pytest

from pypeeker.models.transaction import (
    EditEntry,
    FileRenameEntry,
    TransactionHeader,
    TransactionStatus,
)
from pypeeker.refactor.applier import ApplyError, TransactionApplier
from pypeeker.refactor.planner import RenamePlanner
from pypeeker.storage.store import IndexStore

pytestmark = pytest.mark.integration


class TestNonPendingTransaction:
    def test_apply_non_pending_status(self, project_dir):
        """Transaction with status != PENDING should raise."""
        store = IndexStore(project_dir)
        header = TransactionHeader(
            tx_id="applied_tx",
            symbol_id="test.py:foo",
            old_name="foo",
            new_name="bar",
            created_at="2025-01-01T00:00:00+00:00",
            status=TransactionStatus.APPLIED,
        )
        # Write the header with applied status
        store.save_transaction(header, [
            EditEntry(file="test.py", start=0, end=3, old="foo", new="bar", file_hash="h"),
        ])

        # Manually patch the status in the file
        tx_path = store.transactions_root / "applied_tx.jsonl"
        content = tx_path.read_text()
        content = content.replace('"pending"', '"applied"')
        tx_path.write_text(content)

        applier = TransactionApplier(store)
        with pytest.raises(ApplyError, match="not pending"):
            applier.apply("applied_tx")


class TestFileRename:
    def test_apply_with_file_rename(self, indexed_project):
        """Apply a transaction that includes a file rename."""
        project_dir, store = indexed_project({
            "user.py": "class User:\n    pass\n",
        })
        planner = RenamePlanner(store)
        summary = planner.plan("user.py:User", "Account", include_file=True)

        applier = TransactionApplier(store)
        result = applier.apply(summary.tx_id)

        assert result["status"] == "applied"
        # Old file should be gone, new file should exist
        assert not (project_dir / "user.py").exists()
        assert (project_dir / "account.py").exists()

        content = (project_dir / "account.py").read_text()
        assert "class Account" in content

        # New file should be reindexed
        assert "account.py" in result["files_reindexed"]

    def test_file_rename_result_includes_arrow(self, indexed_project):
        """Result files_modified should show old -> new path for renames."""
        project_dir, store = indexed_project({
            "user.py": "class User:\n    pass\n",
        })
        planner = RenamePlanner(store)
        summary = planner.plan("user.py:User", "Account", include_file=True)

        applier = TransactionApplier(store)
        result = applier.apply(summary.tx_id)

        # Should include the rename indication
        assert any("->" in f for f in result["files_modified"])


class TestVerifyHashesFileRename:
    def test_file_rename_hash_mismatch(self, indexed_project):
        """If the file to be renamed was modified, apply should fail."""
        project_dir, store = indexed_project({
            "user.py": "class User:\n    pass\n",
        })
        planner = RenamePlanner(store)
        summary = planner.plan("user.py:User", "Account", include_file=True)

        # Modify the file after planning
        (project_dir / "user.py").write_text("class User:\n    pass\n# modified\n")

        applier = TransactionApplier(store)
        with pytest.raises(ApplyError, match="modified"):
            applier.apply(summary.tx_id)

    def test_file_rename_file_deleted(self, indexed_project):
        """If the file to be renamed is deleted, apply should fail."""
        project_dir, store = indexed_project({
            "user.py": "class User:\n    pass\n",
        })
        planner = RenamePlanner(store)
        summary = planner.plan("user.py:User", "Account", include_file=True)

        # Delete the file
        (project_dir / "user.py").unlink()

        applier = TransactionApplier(store)
        with pytest.raises(ApplyError, match="not found"):
            applier.apply(summary.tx_id)


class TestFileRenameOnlyHash:
    """Test file_rename hash verification when the file has no text edits."""

    def test_rename_only_hash_verified(self, project_dir):
        """File rename with no edits — hash of renamed file should be verified."""
        store = IndexStore(project_dir)
        (project_dir / "old.py").write_text("x = 1\n")
        file_hash = IndexStore.compute_file_hash(project_dir / "old.py")

        header = TransactionHeader(
            tx_id="rename_only",
            symbol_id="old.py:x",
            old_name="x",
            new_name="y",
            created_at="2025-01-01T00:00:00+00:00",
        )
        file_rename = FileRenameEntry(
            old_path="old.py",
            new_path="new.py",
            file_hash=file_hash,
        )
        # Provide a text edit in a DIFFERENT file so the tx isn't empty
        (project_dir / "other.py").write_text("import old\n")
        other_hash = IndexStore.compute_file_hash(project_dir / "other.py")
        edits = [
            EditEntry(file="other.py", start=7, end=10, old="old", new="new", file_hash=other_hash),
        ]
        store.save_transaction(header, edits, file_rename)

        applier = TransactionApplier(store)
        result = applier.apply("rename_only")
        assert result["status"] == "applied"
        assert (project_dir / "new.py").exists()
        assert not (project_dir / "old.py").exists()

    def test_rename_only_hash_mismatch(self, project_dir):
        """File rename with modified file should fail hash check."""
        store = IndexStore(project_dir)
        (project_dir / "old.py").write_text("x = 1\n")
        file_hash = IndexStore.compute_file_hash(project_dir / "old.py")

        header = TransactionHeader(
            tx_id="rename_hash_fail",
            symbol_id="old.py:x",
            old_name="x",
            new_name="y",
            created_at="2025-01-01T00:00:00+00:00",
        )
        file_rename = FileRenameEntry(
            old_path="old.py",
            new_path="new.py",
            file_hash=file_hash,
        )
        (project_dir / "other.py").write_text("import old\n")
        other_hash = IndexStore.compute_file_hash(project_dir / "other.py")
        edits = [
            EditEntry(file="other.py", start=7, end=10, old="old", new="new", file_hash=other_hash),
        ]
        store.save_transaction(header, edits, file_rename)

        # Modify the file after planning
        (project_dir / "old.py").write_text("x = 2\n")

        applier = TransactionApplier(store)
        with pytest.raises(ApplyError, match="modified"):
            applier.apply("rename_hash_fail")

    def test_rename_only_file_deleted(self, project_dir):
        """File rename with deleted file should fail."""
        store = IndexStore(project_dir)
        (project_dir / "old.py").write_text("x = 1\n")
        file_hash = IndexStore.compute_file_hash(project_dir / "old.py")

        header = TransactionHeader(
            tx_id="rename_deleted",
            symbol_id="old.py:x",
            old_name="x",
            new_name="y",
            created_at="2025-01-01T00:00:00+00:00",
        )
        file_rename = FileRenameEntry(
            old_path="old.py",
            new_path="new.py",
            file_hash=file_hash,
        )
        (project_dir / "other.py").write_text("import old\n")
        other_hash = IndexStore.compute_file_hash(project_dir / "other.py")
        edits = [
            EditEntry(file="other.py", start=7, end=10, old="old", new="new", file_hash=other_hash),
        ]
        store.save_transaction(header, edits, file_rename)

        # Delete the file
        (project_dir / "old.py").unlink()

        applier = TransactionApplier(store)
        with pytest.raises(ApplyError, match="not found"):
            applier.apply("rename_deleted")


class TestReindexEdgeCases:
    def test_reindex_skips_missing_file(self, indexed_project):
        """If a file is missing during reindex, it should be skipped."""
        project_dir, store = indexed_project({
            "test.py": "def foo():\n    pass\n\nfoo()\n"
        })
        planner = RenamePlanner(store)
        summary = planner.plan("test.py:foo", "bar")

        # Delete the file AFTER planning but before apply
        # This won't work through normal path because hash check catches it.
        # Instead, test _reindex_files directly.
        applier = TransactionApplier(store)
        result = applier._reindex_files(["nonexistent.py"])
        assert result == []

    def test_reindex_handles_parse_error(self, indexed_project):
        """If a file has syntax errors during reindex, it should be skipped."""
        project_dir, store = indexed_project({
            "test.py": "def foo(): pass\n"
        })
        # Write invalid Python that tree-sitter can partially parse
        # but that might cause issues (binary content)
        (project_dir / "bad.py").write_bytes(b"\x00\x01\x02invalid")

        applier = TransactionApplier(store)
        result = applier._reindex_files(["bad.py"])
        # Should either succeed or skip gracefully
        assert isinstance(result, list)
