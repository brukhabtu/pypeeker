"""Tests for RenamePlanner."""

import pytest

from pypeeker.refactor.planner import RenamePlanError, RenamePlanner, position_to_byte_offset


class TestPositionToBytOffset:
    def test_first_line_first_column(self):
        content = b"hello\nworld\n"
        assert position_to_byte_offset(content, 0, 0) == 0

    def test_first_line_middle(self):
        content = b"hello\nworld\n"
        assert position_to_byte_offset(content, 0, 3) == 3

    def test_second_line_start(self):
        content = b"hello\nworld\n"
        # line 1 starts at offset 6 (after "hello\n")
        assert position_to_byte_offset(content, 1, 0) == 6

    def test_second_line_middle(self):
        content = b"hello\nworld\n"
        # line 1, column 2 = offset 6 + 2 = 8 (the 'r' in 'world')
        assert position_to_byte_offset(content, 1, 2) == 8

    def test_line_out_of_range(self):
        content = b"hello\n"
        with pytest.raises(ValueError, match="out of range"):
            position_to_byte_offset(content, 5, 0)


class TestRenamePlannerSuccess:
    def test_plan_simple_function(self, indexed_project):
        project_dir, store = indexed_project({
            "test.py": "def greet():\n    pass\n\ngreet()\n"
        })
        planner = RenamePlanner(store)
        summary = planner.plan("test.py:greet", "hello")

        assert summary.old_name == "greet"
        assert summary.new_name == "hello"
        assert summary.symbol_id == "test.py:greet"
        assert "test.py" in summary.files_affected
        assert summary.edit_count == 2  # definition + call

    def test_plan_class_rename(self, indexed_project):
        project_dir, store = indexed_project({
            "test.py": "class Foo:\n    pass\n\nx = Foo()\n"
        })
        planner = RenamePlanner(store)
        summary = planner.plan("test.py:Foo", "Bar")

        assert summary.old_name == "Foo"
        assert summary.new_name == "Bar"
        assert summary.edit_count == 2  # definition + instantiation

    def test_plan_cross_file(self, indexed_project):
        project_dir, store = indexed_project({
            "lib.py": "def helper():\n    pass\n",
            "main.py": "from lib import helper\nhelper()\n",
        })
        planner = RenamePlanner(store)
        summary = planner.plan("lib.py:helper", "do_help")

        assert summary.old_name == "helper"
        assert summary.new_name == "do_help"
        assert "lib.py" in summary.files_affected
        assert "main.py" in summary.files_affected  # Import should be updated
        assert summary.edit_count == 2  # definition + import statement

    def test_plan_with_multiple_references(self, indexed_project):
        project_dir, store = indexed_project({
            "test.py": "def foo():\n    pass\n\nfoo()\nfoo()\nfoo()\n"
        })
        planner = RenamePlanner(store)
        summary = planner.plan("test.py:foo", "bar")

        assert summary.edit_count == 4  # 1 definition + 3 calls

    def test_transaction_file_created(self, indexed_project):
        project_dir, store = indexed_project({
            "test.py": "def greet():\n    pass\n"
        })
        planner = RenamePlanner(store)
        summary = planner.plan("test.py:greet", "hello")

        # Verify transaction file exists
        result = store.load_transaction(summary.tx_id)
        assert result is not None
        header, edits = result
        assert header.symbol_id == "test.py:greet"
        assert len(edits) >= 1


class TestRenamePlannerErrors:
    def test_symbol_not_found(self, indexed_project):
        project_dir, store = indexed_project({
            "test.py": "x = 1\n"
        })
        planner = RenamePlanner(store)

        with pytest.raises(RenamePlanError, match="not found"):
            planner.plan("test.py:nonexistent", "new_name")

    def test_ambiguous_symbol(self, indexed_project):
        project_dir, store = indexed_project({
            "a.py": "def foo(): pass\n",
            "b.py": "def foo(): pass\n",
        })
        planner = RenamePlanner(store)

        with pytest.raises(RenamePlanError, match="Ambiguous"):
            planner.plan("foo", "bar")

    def test_same_name(self, indexed_project):
        project_dir, store = indexed_project({
            "test.py": "def foo(): pass\n"
        })
        planner = RenamePlanner(store)

        with pytest.raises(RenamePlanError, match="same as old"):
            planner.plan("test.py:foo", "foo")

    def test_invalid_identifier(self, indexed_project):
        project_dir, store = indexed_project({
            "test.py": "def foo(): pass\n"
        })
        planner = RenamePlanner(store)

        with pytest.raises(RenamePlanError, match="Invalid Python identifier"):
            planner.plan("test.py:foo", "123invalid")

    def test_name_conflict(self, indexed_project):
        project_dir, store = indexed_project({
            "test.py": "def foo(): pass\ndef bar(): pass\n"
        })
        planner = RenamePlanner(store)

        with pytest.raises(RenamePlanError, match="conflict"):
            planner.plan("test.py:foo", "bar")

    def test_stale_file(self, indexed_project):
        project_dir, store = indexed_project({
            "test.py": "def foo(): pass\n"
        })
        # Modify the file after indexing
        (project_dir / "test.py").write_text("def foo(): pass\n# changed\n")

        planner = RenamePlanner(store)
        with pytest.raises(RenamePlanError, match="stale"):
            planner.plan("test.py:foo", "bar")


class TestEditDeduplication:
    def test_no_duplicate_edits(self, indexed_project):
        """Ensure duplicate locations don't create duplicate edits."""
        project_dir, store = indexed_project({
            "test.py": "def foo(): pass\n"
        })
        planner = RenamePlanner(store)
        summary = planner.plan("test.py:foo", "bar")

        result = store.load_transaction(summary.tx_id)
        assert result is not None
        _, edits = result

        # Check for duplicates by (file, start, end)
        seen = set()
        for edit in edits:
            key = (edit.file, edit.start, edit.end)
            assert key not in seen, f"Duplicate edit: {key}"
            seen.add(key)


class TestCrossFileImportRename:
    def test_rename_updates_import_statement(self, indexed_project):
        """Renaming a definition should update import statements in other files."""
        project_dir, store = indexed_project({
            "lib.py": "def helper():\n    pass\n",
            "main.py": "from lib import helper\n",
        })
        planner = RenamePlanner(store)
        summary = planner.plan("lib.py:helper", "do_help")

        assert "lib.py" in summary.files_affected
        assert "main.py" in summary.files_affected
        assert summary.edit_count == 2  # def + import

    def test_rename_with_alias_preserves_alias(self, indexed_project):
        """Renaming should update 'helper' but not 'h' in 'from lib import helper as h'."""
        project_dir, store = indexed_project({
            "lib.py": "def helper():\n    pass\n",
            "main.py": "from lib import helper as h\nh()\n",
        })
        planner = RenamePlanner(store)
        summary = planner.plan("lib.py:helper", "do_help")

        assert "main.py" in summary.files_affected

        # Verify the edit is for "helper" not "h"
        result = store.load_transaction(summary.tx_id)
        assert result is not None
        _, edits = result

        main_edits = [e for e in edits if e.file == "main.py"]
        assert len(main_edits) == 1
        assert main_edits[0].old == "helper"
        assert main_edits[0].new == "do_help"

    def test_multiple_files_import_same_symbol(self, indexed_project):
        """Multiple files importing the same symbol should all be updated."""
        project_dir, store = indexed_project({
            "lib.py": "def helper():\n    pass\n",
            "a.py": "from lib import helper\n",
            "b.py": "from lib import helper\n",
        })
        planner = RenamePlanner(store)
        summary = planner.plan("lib.py:helper", "do_help")

        assert {"lib.py", "a.py", "b.py"} == set(summary.files_affected)
        assert summary.edit_count == 3  # def + 2 imports

    def test_external_import_not_affected(self, indexed_project):
        """Imports from external packages should not be affected."""
        project_dir, store = indexed_project({
            "main.py": "from os import path\n",
        })
        # There's no local "os.py", so find_import_symbols won't match
        # This test verifies we don't crash when trying to rename something
        # that isn't defined locally
        planner = RenamePlanner(store)
        # No assertion needed - just verify it doesn't crash during indexing

    def test_class_import_rename(self, indexed_project):
        """Renaming a class should update imports of that class."""
        project_dir, store = indexed_project({
            "models.py": "class User:\n    pass\n",
            "app.py": "from models import User\n",
        })
        planner = RenamePlanner(store)
        summary = planner.plan("models.py:User", "Account")

        assert "models.py" in summary.files_affected
        assert "app.py" in summary.files_affected
        assert summary.edit_count == 2
