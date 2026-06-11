"""Tests for TransactionApplier."""

import pytest

from pypeeker.models.transaction import (
    EditEntry,
    TransactionHeader,
    TransactionStatus,
)
from pypeeker.refactor.applier import ApplyError, RollbackError, TransactionApplier
from pypeeker.refactor.planner import RenamePlanner
from pypeeker.storage import IndexStore, TransactionStore


class TestApplierSuccess:
    def test_apply_single_file(self, indexed_project):
        project_dir, store = indexed_project({
            "test.py": "def foo():\n    pass\n\nfoo()\n"
        })
        planner = RenamePlanner(store, TransactionStore(store.project_root))
        summary = planner.plan("test:foo", "bar")

        applier = TransactionApplier(store, TransactionStore(store.project_root))
        result = applier.apply(summary.tx_id)

        assert result["status"] == "applied"
        assert "test.py" in result["files_modified"]
        assert "test.py" in result["files_reindexed"]

        # Verify file contents changed
        content = (project_dir / "test.py").read_text()
        assert "def bar(" in content
        assert "bar()" in content
        assert "foo" not in content

    def test_apply_preserves_formatting(self, indexed_project):
        project_dir, store = indexed_project({
            "test.py": "def   greet(name):\n    \"\"\"Doc.\"\"\"\n    return name\n"
        })
        planner = RenamePlanner(store, TransactionStore(store.project_root))
        summary = planner.plan("test:greet", "hello")

        applier = TransactionApplier(store, TransactionStore(store.project_root))
        applier.apply(summary.tx_id)

        content = (project_dir / "test.py").read_text()
        # Only the name should change, not spacing
        assert "def   hello(name):" in content
        assert '"""Doc."""' in content

    def test_apply_multiple_edits_same_file(self, indexed_project):
        project_dir, store = indexed_project({
            "test.py": "def foo():\n    pass\n\nfoo()\nfoo()\nfoo()\n"
        })
        planner = RenamePlanner(store, TransactionStore(store.project_root))
        summary = planner.plan("test:foo", "bar")

        applier = TransactionApplier(store, TransactionStore(store.project_root))
        applier.apply(summary.tx_id)

        content = (project_dir / "test.py").read_text()
        assert content.count("bar") == 4
        assert "foo" not in content

    def test_apply_reindexes_file(self, indexed_project):
        project_dir, store = indexed_project({
            "test.py": "def foo(): pass\n"
        })
        planner = RenamePlanner(store, TransactionStore(store.project_root))
        summary = planner.plan("test:foo", "bar")

        applier = TransactionApplier(store, TransactionStore(store.project_root))
        applier.apply(summary.tx_id)

        # Verify the index reflects the new name
        index = store.load("test.py")
        assert index is not None
        symbol_names = [s.name for s in index.symbols]
        assert "bar" in symbol_names
        assert "foo" not in symbol_names

    def test_apply_reports_empty_reindex_failures_on_success(self, indexed_project):
        project_dir, store = indexed_project({
            "test.py": "def foo(): pass\n"
        })
        planner = RenamePlanner(store, TransactionStore(store.project_root))
        summary = planner.plan("test:foo", "bar")

        applier = TransactionApplier(store, TransactionStore(store.project_root))
        result = applier.apply(summary.tx_id)

        assert result["files_reindex_failed"] == []

    def test_transaction_marked_applied_after_apply(self, indexed_project):
        project_dir, store = indexed_project({
            "test.py": "def foo(): pass\n"
        })
        planner = RenamePlanner(store, TransactionStore(store.project_root))
        summary = planner.plan("test:foo", "bar")
        tx_id = summary.tx_id

        applier = TransactionApplier(store, TransactionStore(store.project_root))
        applier.apply(tx_id)

        # Transaction is retained on disk with status APPLIED (for rollback)
        loaded = TransactionStore(store.project_root).load(tx_id)
        assert loaded is not None
        header, edits, _ = loaded
        assert header.status == TransactionStatus.APPLIED
        assert edits  # edit lines preserved for rollback

    def test_reapply_of_applied_transaction_refused(self, indexed_project):
        project_dir, store = indexed_project({
            "test.py": "def foo(): pass\n"
        })
        planner = RenamePlanner(store, TransactionStore(store.project_root))
        summary = planner.plan("test:foo", "bar")
        tx_id = summary.tx_id

        applier = TransactionApplier(store, TransactionStore(store.project_root))
        applier.apply(tx_id)

        with pytest.raises(ApplyError, match="not pending"):
            applier.apply(tx_id)


class TestReindexFailures:
    def test_reindex_failure_surfaced_in_result(self, indexed_project, monkeypatch):
        """A re-index failure must appear in files_reindex_failed, not be swallowed."""
        project_dir, store = indexed_project({
            "test.py": "def foo():\n    pass\n\nfoo()\n"
        })
        planner = RenamePlanner(store, TransactionStore(store.project_root))
        summary = planner.plan("test:foo", "bar")

        def boom(self, file_index):
            raise OSError("disk full")

        monkeypatch.setattr(IndexStore, "save", boom)

        applier = TransactionApplier(store, TransactionStore(store.project_root))
        result = applier.apply(summary.tx_id)

        # Apply still succeeds: the edits are already on disk
        assert result["status"] == "applied"
        assert "test.py" in result["files_modified"]
        content = (project_dir / "test.py").read_text()
        assert "def bar(" in content

        # But the index inconsistency is visible in the output
        assert result["files_reindexed"] == []
        assert result["files_reindex_failed"] == [
            {"file": "test.py", "error": "disk full"}
        ]

    def test_reindex_failure_only_affects_failing_file(
        self, indexed_project, monkeypatch
    ):
        """Files that re-index fine still succeed when another file fails."""
        project_dir, store = indexed_project({
            "lib.py": "def foo():\n    pass\n",
            "main.py": "from lib import foo\n\nfoo()\n",
        })
        planner = RenamePlanner(store, TransactionStore(store.project_root))
        summary = planner.plan("lib:foo", "bar")

        original_save = IndexStore.save

        def selective_boom(self, file_index):
            if file_index.file_path == "main.py":
                raise ValueError("binder choked")
            return original_save(self, file_index)

        monkeypatch.setattr(IndexStore, "save", selective_boom)

        applier = TransactionApplier(store, TransactionStore(store.project_root))
        result = applier.apply(summary.tx_id)

        assert result["status"] == "applied"
        assert result["files_reindexed"] == ["lib.py"]
        assert result["files_reindex_failed"] == [
            {"file": "main.py", "error": "binder choked"}
        ]


class TestApplierErrors:
    def test_transaction_not_found(self, project_dir):
        store = IndexStore(project_dir)
        applier = TransactionApplier(store, TransactionStore(store.project_root))

        with pytest.raises(ApplyError, match="not found"):
            applier.apply("nonexistent_tx")

    def test_file_modified_since_plan(self, indexed_project):
        project_dir, store = indexed_project({
            "test.py": "def foo(): pass\n"
        })
        planner = RenamePlanner(store, TransactionStore(store.project_root))
        summary = planner.plan("test:foo", "bar")

        # Modify file after planning
        (project_dir / "test.py").write_text("def foo(): pass\n# changed\n")

        applier = TransactionApplier(store, TransactionStore(store.project_root))
        with pytest.raises(ApplyError, match="modified"):
            applier.apply(summary.tx_id)

        # Pre-flight failure: nothing was touched, transaction stays PENDING
        loaded = TransactionStore(store.project_root).load(summary.tx_id)
        assert loaded is not None
        assert loaded[0].status == TransactionStatus.PENDING

    def test_file_deleted(self, indexed_project):
        project_dir, store = indexed_project({
            "test.py": "def foo(): pass\n"
        })
        planner = RenamePlanner(store, TransactionStore(store.project_root))
        summary = planner.plan("test:foo", "bar")

        # Delete the file
        (project_dir / "test.py").unlink()

        applier = TransactionApplier(store, TransactionStore(store.project_root))
        with pytest.raises(ApplyError, match="not found"):
            applier.apply(summary.tx_id)

    def test_empty_transaction(self, project_dir):
        store = IndexStore(project_dir)
        header = TransactionHeader(
            tx_id="empty_tx",
            symbol_id="test:foo",
            old_name="foo",
            new_name="bar",
            created_at="2025-01-01T00:00:00+00:00",
        )
        TransactionStore(store.project_root).save(header, [])

        applier = TransactionApplier(store, TransactionStore(store.project_root))
        with pytest.raises(ApplyError, match="no edits"):
            applier.apply("empty_tx")


class TestBottomToTopOrdering:
    def test_multiple_edits_applied_correctly(self, indexed_project):
        """Edits at different positions should all be applied correctly."""
        project_dir, store = indexed_project({
            "test.py": "x = 1\ny = 2\nz = 3\n"
        })

        # Create a manual transaction with multiple edits
        file_hash = IndexStore.compute_file_hash(project_dir / "test.py")
        header = TransactionHeader(
            tx_id="multi_edit",
            symbol_id="test:x",
            old_name="x",
            new_name="a",
            created_at="2025-01-01T00:00:00+00:00",
        )
        # Note: these byte offsets are for "x = 1\ny = 2\nz = 3\n"
        # x is at 0, y is at 6, z is at 12
        edits = [
            EditEntry(file="test.py", start=0, end=1, old="x", new="a", file_hash=file_hash),
            EditEntry(file="test.py", start=6, end=7, old="y", new="b", file_hash=file_hash),
            EditEntry(file="test.py", start=12, end=13, old="z", new="c", file_hash=file_hash),
        ]
        TransactionStore(store.project_root).save(header, edits)

        applier = TransactionApplier(store, TransactionStore(store.project_root))
        applier.apply("multi_edit")

        content = (project_dir / "test.py").read_text()
        assert "a = 1" in content
        assert "b = 2" in content
        assert "c = 3" in content


class TestContentVerification:
    def test_content_mismatch_detected(self, project_dir):
        """If the old text doesn't match, apply should fail."""
        store = IndexStore(project_dir)
        (project_dir / "test.py").write_text("hello world\n")
        file_hash = IndexStore.compute_file_hash(project_dir / "test.py")

        header = TransactionHeader(
            tx_id="mismatch_tx",
            symbol_id="test:x",
            old_name="foo",
            new_name="bar",
            created_at="2025-01-01T00:00:00+00:00",
        )
        # The edit claims old text is "foo" but the file has "hello"
        edits = [
            EditEntry(file="test.py", start=0, end=5, old="foo", new="bar", file_hash=file_hash),
        ]
        TransactionStore(store.project_root).save(header, edits)

        applier = TransactionApplier(store, TransactionStore(store.project_root))
        with pytest.raises(ApplyError, match="mismatch"):
            applier.apply("mismatch_tx")

        # Mid-apply failure: rolled back and marked FAILED, retained on disk
        loaded = TransactionStore(store.project_root).load("mismatch_tx")
        assert loaded is not None
        assert loaded[0].status == TransactionStatus.FAILED
        assert (project_dir / "test.py").read_text() == "hello world\n"

        # FAILED transactions cannot be re-applied
        with pytest.raises(ApplyError, match="not pending"):
            applier.apply("mismatch_tx")


class TestRollback:
    def _plan_and_apply(self, store, symbol_id, new_name, **plan_kwargs):
        tx_store = TransactionStore(store.project_root)
        planner = RenamePlanner(store, tx_store)
        summary = planner.plan(symbol_id, new_name, **plan_kwargs)
        applier = TransactionApplier(store, tx_store)
        applier.apply(summary.tx_id)
        return summary.tx_id, applier, tx_store

    def test_rollback_round_trip_restores_bytes(self, indexed_project):
        original = "def foo():\n    pass\n\nfoo()\nfoo()\n"
        project_dir, store = indexed_project({"test.py": original})
        tx_id, applier, tx_store = self._plan_and_apply(store, "test:foo", "bar")

        # Sanity: apply changed the file and marked the tx APPLIED
        assert "bar" in (project_dir / "test.py").read_text()
        assert tx_store.load(tx_id)[0].status == TransactionStatus.APPLIED

        result = applier.rollback(tx_id)

        assert result["status"] == "rolled_back"
        assert result["files_restored"] == ["test.py"]
        assert result["files_reindexed"] == ["test.py"]
        assert result["files_reindex_failed"] == []
        # Byte-identical restore
        assert (project_dir / "test.py").read_bytes() == original.encode("utf-8")
        # Full lifecycle: PENDING -> APPLIED -> ROLLED_BACK
        assert tx_store.load(tx_id)[0].status == TransactionStatus.ROLLED_BACK

    def test_rollback_with_length_changing_edits(self, indexed_project):
        """Offsets shift when new and old names differ in length."""
        original = "def f():\n    pass\n\nf()\nf()\nf()\n"
        project_dir, store = indexed_project({"test.py": original})
        tx_id, applier, _ = self._plan_and_apply(
            store, "test:f", "much_longer_name"
        )

        applier.rollback(tx_id)

        assert (project_dir / "test.py").read_bytes() == original.encode("utf-8")

    def test_rollback_reindexes_old_name(self, indexed_project):
        project_dir, store = indexed_project({"test.py": "def foo(): pass\n"})
        tx_id, applier, _ = self._plan_and_apply(store, "test:foo", "bar")

        applier.rollback(tx_id)

        index = store.load("test.py")
        assert index is not None
        symbol_names = [s.name for s in index.symbols]
        assert "foo" in symbol_names
        assert "bar" not in symbol_names

    def test_rollback_reverses_file_rename(self, indexed_project):
        original = "def foo():\n    pass\n"
        project_dir, store = indexed_project({"foo.py": original})
        tx_id, applier, tx_store = self._plan_and_apply(
            store, "foo:foo", "bar", include_file=True
        )

        # Sanity: apply renamed the file
        assert not (project_dir / "foo.py").exists()
        assert (project_dir / "bar.py").exists()

        result = applier.rollback(tx_id)

        assert (project_dir / "foo.py").read_bytes() == original.encode("utf-8")
        assert not (project_dir / "bar.py").exists()
        assert "bar.py -> foo.py" in result["files_restored"]
        # Index: old path restored, new path removed
        assert store.load("foo.py") is not None
        assert store.load("bar.py") is None
        assert tx_store.load(tx_id)[0].status == TransactionStatus.ROLLED_BACK

    def test_rollback_refuses_pending(self, indexed_project):
        project_dir, store = indexed_project({"test.py": "def foo(): pass\n"})
        tx_store = TransactionStore(store.project_root)
        summary = RenamePlanner(store, tx_store).plan("test:foo", "bar")

        applier = TransactionApplier(store, tx_store)
        with pytest.raises(RollbackError, match="not applied"):
            applier.rollback(summary.tx_id)

        # Untouched: still PENDING, file unchanged
        assert tx_store.load(summary.tx_id)[0].status == TransactionStatus.PENDING
        assert "foo" in (project_dir / "test.py").read_text()

    def test_rollback_refuses_already_rolled_back(self, indexed_project):
        project_dir, store = indexed_project({"test.py": "def foo(): pass\n"})
        tx_id, applier, _ = self._plan_and_apply(store, "test:foo", "bar")
        applier.rollback(tx_id)

        with pytest.raises(RollbackError, match="not applied"):
            applier.rollback(tx_id)

    def test_rollback_not_found(self, project_dir):
        store = IndexStore(project_dir)
        applier = TransactionApplier(store, TransactionStore(project_dir))

        with pytest.raises(RollbackError, match="not found"):
            applier.rollback("nonexistent_tx")

    def test_rollback_refuses_when_edited_span_modified(self, indexed_project):
        project_dir, store = indexed_project({
            "test.py": "def foo():\n    pass\n\nfoo()\n"
        })
        tx_id, applier, tx_store = self._plan_and_apply(store, "test:foo", "bar")

        # Hand-edit the renamed symbol after apply
        content = (project_dir / "test.py").read_text()
        modified = content.replace("def bar(", "def baz(")
        (project_dir / "test.py").write_text(modified)

        with pytest.raises(RollbackError, match="modified"):
            applier.rollback(tx_id)

        # No partial rollback: file untouched, status still APPLIED
        assert (project_dir / "test.py").read_text() == modified
        assert tx_store.load(tx_id)[0].status == TransactionStatus.APPLIED

    def test_rollback_refuses_when_file_modified_outside_spans(
        self, indexed_project
    ):
        project_dir, store = indexed_project({
            "test.py": "def foo():\n    pass\n\nfoo()\n"
        })
        tx_id, applier, tx_store = self._plan_and_apply(store, "test:foo", "bar")

        # Append unrelated content after apply: every edited span still holds
        # the new text, but the restored bytes no longer hash to the
        # plan-time file hash.
        path = project_dir / "test.py"
        modified = path.read_text() + "# unrelated change\n"
        path.write_text(modified)

        with pytest.raises(RollbackError, match="modified"):
            applier.rollback(tx_id)

        assert path.read_text() == modified
        assert tx_store.load(tx_id)[0].status == TransactionStatus.APPLIED

    def test_rollback_refuses_failed_transaction(self, project_dir):
        store = IndexStore(project_dir)
        (project_dir / "test.py").write_text("hello world\n")
        file_hash = IndexStore.compute_file_hash(project_dir / "test.py")

        header = TransactionHeader(
            tx_id="failing_tx",
            symbol_id="test:x",
            old_name="foo",
            new_name="bar",
            created_at="2025-01-01T00:00:00+00:00",
        )
        # old text doesn't match -> apply fails mid-way and marks FAILED
        edits = [
            EditEntry(file="test.py", start=0, end=5, old="foo", new="bar",
                      file_hash=file_hash),
        ]
        tx_store = TransactionStore(project_dir)
        tx_store.save(header, edits)
        applier = TransactionApplier(store, tx_store)
        with pytest.raises(ApplyError):
            applier.apply("failing_tx")
        assert tx_store.load("failing_tx")[0].status == TransactionStatus.FAILED

        with pytest.raises(RollbackError, match="not applied"):
            applier.rollback("failing_tx")

    def test_reapply_after_rollback_refused(self, indexed_project):
        """ROLLED_BACK is terminal: the transaction cannot be re-applied."""
        project_dir, store = indexed_project({"test.py": "def foo(): pass\n"})
        tx_id, applier, _ = self._plan_and_apply(store, "test:foo", "bar")
        applier.rollback(tx_id)

        with pytest.raises(ApplyError, match="not pending"):
            applier.apply(tx_id)


class TestInsertDeleteEdits:
    """INSERT/DELETE ops apply via the same byte-splice mechanism as REPLACE."""

    def _save_tx(self, store, file_path, edits):
        import uuid
        from datetime import datetime, timezone
        ts = TransactionStore(store.project_root)
        header = TransactionHeader(
            tx_id=uuid.uuid4().hex[:12], symbol_id="x", old_name="x",
            new_name="x", created_at=datetime.now(timezone.utc).isoformat(),
            operation="edit",
        )
        ts.save(header, edits, None)
        return header.tx_id

    def test_insert(self, indexed_project):
        from pypeeker.models.transaction import EditEntry, EditOp
        project, store = indexed_project({"m.py": "a = 1\n"})
        fh = IndexStore.compute_file_hash(project / "m.py")
        # insert "b = 2\n" at byte 0 (start == end, old == "")
        tx = self._save_tx(store, "m.py", [
            EditEntry(file="m.py", start=0, end=0, old="", new="b = 2\n",
                      file_hash=fh, op=EditOp.INSERT)
        ])
        TransactionApplier(store, TransactionStore(store.project_root)).apply(tx)
        assert (project / "m.py").read_text() == "b = 2\na = 1\n"

    def test_delete(self, indexed_project):
        from pypeeker.models.transaction import EditEntry, EditOp
        project, store = indexed_project({"m.py": "a = 1\nb = 2\n"})
        fh = IndexStore.compute_file_hash(project / "m.py")
        # delete "a = 1\n" (bytes 0..6), new == ""
        tx = self._save_tx(store, "m.py", [
            EditEntry(file="m.py", start=0, end=6, old="a = 1\n", new="",
                      file_hash=fh, op=EditOp.DELETE)
        ])
        TransactionApplier(store, TransactionStore(store.project_root)).apply(tx)
        assert (project / "m.py").read_text() == "b = 2\n"

    def test_mixed_insert_replace_delete(self, indexed_project):
        from pypeeker.models.transaction import EditEntry, EditOp
        project, store = indexed_project({"m.py": "a = 1\nb = 2\nc = 3\n"})
        fh = IndexStore.compute_file_hash(project / "m.py")
        tx = self._save_tx(store, "m.py", [
            EditEntry(file="m.py", start=0, end=0, old="", new="head = 0\n",
                      file_hash=fh, op=EditOp.INSERT),
            EditEntry(file="m.py", start=6, end=12, old="b = 2\n", new="",
                      file_hash=fh, op=EditOp.DELETE),
            EditEntry(file="m.py", start=12, end=13, old="c", new="C",
                      file_hash=fh, op=EditOp.REPLACE),
        ])
        TransactionApplier(store, TransactionStore(store.project_root)).apply(tx)
        assert (project / "m.py").read_text() == "head = 0\na = 1\nC = 3\n"
