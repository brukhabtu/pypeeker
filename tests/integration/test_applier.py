"""Tests for TransactionApplier."""

import pytest

from pypeeker.models.transaction import EditEntry, TransactionHeader
from pypeeker.refactor.applier import ApplyError, TransactionApplier
from pypeeker.refactor.planner import RenamePlanner
from pypeeker.storage.store import IndexStore

pytestmark = pytest.mark.integration


class TestApplierSuccess:
    def test_apply_single_file(self, indexed_project):
        project_dir, store = indexed_project({
            "test.py": "def foo():\n    pass\n\nfoo()\n"
        })
        planner = RenamePlanner(store)
        summary = planner.plan("test.py:foo", "bar")

        applier = TransactionApplier(store)
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
        planner = RenamePlanner(store)
        summary = planner.plan("test.py:greet", "hello")

        applier = TransactionApplier(store)
        applier.apply(summary.tx_id)

        content = (project_dir / "test.py").read_text()
        # Only the name should change, not spacing
        assert "def   hello(name):" in content
        assert '"""Doc."""' in content

    def test_apply_multiple_edits_same_file(self, indexed_project):
        project_dir, store = indexed_project({
            "test.py": "def foo():\n    pass\n\nfoo()\nfoo()\nfoo()\n"
        })
        planner = RenamePlanner(store)
        summary = planner.plan("test.py:foo", "bar")

        applier = TransactionApplier(store)
        applier.apply(summary.tx_id)

        content = (project_dir / "test.py").read_text()
        assert content.count("bar") == 4
        assert "foo" not in content

    def test_apply_reindexes_file(self, indexed_project):
        project_dir, store = indexed_project({
            "test.py": "def foo(): pass\n"
        })
        planner = RenamePlanner(store)
        summary = planner.plan("test.py:foo", "bar")

        applier = TransactionApplier(store)
        applier.apply(summary.tx_id)

        # Verify the index reflects the new name
        index = store.load("test.py")
        assert index is not None
        symbol_names = [s.name for s in index.symbols]
        assert "bar" in symbol_names
        assert "foo" not in symbol_names

    def test_transaction_removed_after_apply(self, indexed_project):
        project_dir, store = indexed_project({
            "test.py": "def foo(): pass\n"
        })
        planner = RenamePlanner(store)
        summary = planner.plan("test.py:foo", "bar")
        tx_id = summary.tx_id

        applier = TransactionApplier(store)
        applier.apply(tx_id)

        # Transaction should be removed
        assert store.load_transaction(tx_id) is None


class TestApplierErrors:
    def test_transaction_not_found(self, project_dir):
        store = IndexStore(project_dir)
        applier = TransactionApplier(store)

        with pytest.raises(ApplyError, match="not found"):
            applier.apply("nonexistent_tx")

    def test_file_modified_since_plan(self, indexed_project):
        project_dir, store = indexed_project({
            "test.py": "def foo(): pass\n"
        })
        planner = RenamePlanner(store)
        summary = planner.plan("test.py:foo", "bar")

        # Modify file after planning
        (project_dir / "test.py").write_text("def foo(): pass\n# changed\n")

        applier = TransactionApplier(store)
        with pytest.raises(ApplyError, match="modified"):
            applier.apply(summary.tx_id)

    def test_file_deleted(self, indexed_project):
        project_dir, store = indexed_project({
            "test.py": "def foo(): pass\n"
        })
        planner = RenamePlanner(store)
        summary = planner.plan("test.py:foo", "bar")

        # Delete the file
        (project_dir / "test.py").unlink()

        applier = TransactionApplier(store)
        with pytest.raises(ApplyError, match="not found"):
            applier.apply(summary.tx_id)

    def test_empty_transaction(self, project_dir):
        store = IndexStore(project_dir)
        header = TransactionHeader(
            tx_id="empty_tx",
            symbol_id="test.py:foo",
            old_name="foo",
            new_name="bar",
            created_at="2025-01-01T00:00:00+00:00",
        )
        store.save_transaction(header, [])

        applier = TransactionApplier(store)
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
            symbol_id="test.py:x",
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
        store.save_transaction(header, edits)

        applier = TransactionApplier(store)
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
            symbol_id="test.py:x",
            old_name="foo",
            new_name="bar",
            created_at="2025-01-01T00:00:00+00:00",
        )
        # The edit claims old text is "foo" but the file has "hello"
        edits = [
            EditEntry(file="test.py", start=0, end=5, old="foo", new="bar", file_hash=file_hash),
        ]
        store.save_transaction(header, edits)

        applier = TransactionApplier(store)
        with pytest.raises(ApplyError, match="mismatch"):
            applier.apply("mismatch_tx")
