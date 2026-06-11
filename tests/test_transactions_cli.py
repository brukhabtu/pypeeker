"""Tests for the transactions CLI group (list/show/cancel) and rollback."""

from __future__ import annotations

import json
import os
from pathlib import Path

from click.testing import CliRunner

from pypeeker.cli import main


def _make_project(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create a project directory with source files and pyproject.toml."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
    (tmp_path / ".semantic-tool" / "index").mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return tmp_path


def _plan(runner: CliRunner, project: Path, source_file: str = "test.py") -> str:
    """Index the project and plan a rename; returns the tx_id."""
    result = runner.invoke(
        main, ["index", str(project / source_file)], catch_exceptions=False
    )
    assert result.exit_code == 0
    result = runner.invoke(
        main, ["plan-rename", "test:foo", "bar"], catch_exceptions=False
    )
    assert result.exit_code == 0
    return json.loads(result.output)["tx_id"]


class TestHelp:
    def test_transactions_group_help_documents_lifecycle(self):
        runner = CliRunner()
        result = runner.invoke(main, ["transactions", "--help"])
        assert result.exit_code == 0
        assert "PENDING" in result.output
        assert "APPLIED" in result.output
        assert "ROLLED_BACK" in result.output
        for subcommand in ("list", "show", "cancel"):
            assert subcommand in result.output

    def test_rollback_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["rollback", "--help"])
        assert result.exit_code == 0
        assert "Roll back an applied transaction" in result.output
        assert "TX_ID" in result.output
        assert "ROLLED_BACK" in result.output

    def test_cancel_help_mentions_pending_only(self):
        runner = CliRunner()
        result = runner.invoke(main, ["transactions", "cancel", "--help"])
        assert result.exit_code == 0
        assert "pending" in result.output.lower()

    def test_commands_appear_in_main_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "transactions" in result.output
        assert "rollback" in result.output


class TestList:
    def test_list_empty(self, tmp_path):
        project = _make_project(tmp_path, {})
        runner = CliRunner()
        os.chdir(project)

        result = runner.invoke(main, ["transactions", "list"], catch_exceptions=False)
        assert result.exit_code == 0
        assert json.loads(result.output) == []

    def test_list_shows_pending_transaction(self, tmp_path):
        project = _make_project(
            tmp_path, {"test.py": "def foo():\n    pass\n\nfoo()\n"}
        )
        runner = CliRunner()
        os.chdir(project)
        tx_id = _plan(runner, project)

        result = runner.invoke(main, ["transactions", "list"], catch_exceptions=False)
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert len(output) == 1
        entry = output[0]
        assert entry["tx_id"] == tx_id
        assert entry["operation"] == "rename"
        assert entry["status"] == "pending"
        assert entry["edit_count"] == 2
        assert entry["files_affected"] == ["test.py"]
        assert "created_at" in entry

    def test_list_reflects_status_transitions(self, tmp_path):
        project = _make_project(
            tmp_path, {"test.py": "def foo():\n    pass\n\nfoo()\n"}
        )
        runner = CliRunner()
        os.chdir(project)
        tx_id = _plan(runner, project)

        result = runner.invoke(main, ["apply", tx_id], catch_exceptions=False)
        assert result.exit_code == 0
        output = json.loads(
            runner.invoke(
                main, ["transactions", "list"], catch_exceptions=False
            ).output
        )
        assert output[0]["status"] == "applied"

        result = runner.invoke(main, ["rollback", tx_id], catch_exceptions=False)
        assert result.exit_code == 0
        output = json.loads(
            runner.invoke(
                main, ["transactions", "list"], catch_exceptions=False
            ).output
        )
        assert output[0]["status"] == "rolled_back"


class TestShow:
    def test_show_transaction(self, tmp_path):
        project = _make_project(
            tmp_path, {"test.py": "def foo():\n    pass\n\nfoo()\n"}
        )
        runner = CliRunner()
        os.chdir(project)
        tx_id = _plan(runner, project)

        result = runner.invoke(
            main, ["transactions", "show", tx_id], catch_exceptions=False
        )
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["header"]["tx_id"] == tx_id
        assert output["header"]["status"] == "pending"
        assert output["header"]["old_name"] == "foo"
        assert output["header"]["new_name"] == "bar"
        assert output["file_rename"] is None
        assert len(output["edits"]) == 2
        for edit in output["edits"]:
            assert edit["file"] == "test.py"
            assert edit["old"] == "foo"
            assert edit["new"] == "bar"
            assert "start" in edit
            assert "end" in edit

    def test_show_not_found(self, tmp_path):
        project = _make_project(tmp_path, {})
        runner = CliRunner()
        os.chdir(project)

        result = runner.invoke(main, ["transactions", "show", "nonexistent_tx"])
        assert result.exit_code != 0
        output = json.loads(result.output)
        assert "not found" in output["error"]


class TestCancel:
    def test_cancel_pending_transaction(self, tmp_path):
        project = _make_project(tmp_path, {"test.py": "def foo(): pass\n"})
        runner = CliRunner()
        os.chdir(project)
        tx_id = _plan(runner, project)

        result = runner.invoke(
            main, ["transactions", "cancel", tx_id], catch_exceptions=False
        )
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output == {"tx_id": tx_id, "status": "cancelled"}

        # The transaction is gone
        result = runner.invoke(main, ["transactions", "list"], catch_exceptions=False)
        assert json.loads(result.output) == []
        result = runner.invoke(main, ["transactions", "show", tx_id])
        assert result.exit_code != 0

    def test_cancel_refuses_applied_transaction(self, tmp_path):
        project = _make_project(tmp_path, {"test.py": "def foo(): pass\n"})
        runner = CliRunner()
        os.chdir(project)
        tx_id = _plan(runner, project)
        runner.invoke(main, ["apply", tx_id], catch_exceptions=False)

        result = runner.invoke(main, ["transactions", "cancel", tx_id])
        assert result.exit_code != 0
        output = json.loads(result.output)
        assert "pending" in output["error"]
        assert "applied" in output["error"]

        # Still on disk, still applied
        result = runner.invoke(main, ["transactions", "list"], catch_exceptions=False)
        assert json.loads(result.output)[0]["status"] == "applied"

    def test_cancel_not_found(self, tmp_path):
        project = _make_project(tmp_path, {})
        runner = CliRunner()
        os.chdir(project)

        result = runner.invoke(main, ["transactions", "cancel", "nonexistent_tx"])
        assert result.exit_code != 0
        output = json.loads(result.output)
        assert "not found" in output["error"]


class TestRollbackCommand:
    def test_rollback_round_trip(self, tmp_path):
        original = "def foo():\n    pass\n\nfoo()\n"
        project = _make_project(tmp_path, {"test.py": original})
        runner = CliRunner()
        os.chdir(project)
        tx_id = _plan(runner, project)
        runner.invoke(main, ["apply", tx_id], catch_exceptions=False)
        assert "bar" in (project / "test.py").read_text()

        result = runner.invoke(main, ["rollback", tx_id], catch_exceptions=False)
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["tx_id"] == tx_id
        assert output["status"] == "rolled_back"
        assert output["files_restored"] == ["test.py"]
        assert output["files_reindexed"] == ["test.py"]
        assert output["files_reindex_failed"] == []

        # Content restored byte-for-byte
        assert (project / "test.py").read_bytes() == original.encode("utf-8")

    def test_rollback_refuses_pending(self, tmp_path):
        project = _make_project(tmp_path, {"test.py": "def foo(): pass\n"})
        runner = CliRunner()
        os.chdir(project)
        tx_id = _plan(runner, project)

        result = runner.invoke(main, ["rollback", tx_id])
        assert result.exit_code != 0
        output = json.loads(result.output)
        assert "not applied" in output["error"]

    def test_rollback_refuses_modified_file(self, tmp_path):
        project = _make_project(
            tmp_path, {"test.py": "def foo():\n    pass\n\nfoo()\n"}
        )
        runner = CliRunner()
        os.chdir(project)
        tx_id = _plan(runner, project)
        runner.invoke(main, ["apply", tx_id], catch_exceptions=False)

        # Modify the file after apply
        path = project / "test.py"
        path.write_text(path.read_text() + "# extra\n")

        result = runner.invoke(main, ["rollback", tx_id])
        assert result.exit_code != 0
        output = json.loads(result.output)
        assert "modified" in output["error"]

    def test_rollback_not_found(self, tmp_path):
        project = _make_project(tmp_path, {})
        runner = CliRunner()
        os.chdir(project)

        result = runner.invoke(main, ["rollback", "nonexistent_tx"])
        assert result.exit_code != 0
        output = json.loads(result.output)
        assert "not found" in output["error"]
