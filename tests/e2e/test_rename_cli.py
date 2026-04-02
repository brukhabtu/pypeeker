"""Tests for plan-rename and apply CLI commands."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from pypeeker.cli import main

pytestmark = pytest.mark.e2e


def _make_project(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create a project directory with source files and pyproject.toml."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
    (tmp_path / ".semantic-tool" / "index").mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return tmp_path


def test_plan_rename_help():
    runner = CliRunner()
    result = runner.invoke(main, ["plan-rename", "--help"])
    assert result.exit_code == 0
    assert "Plan a symbol rename" in result.output
    assert "SYMBOL_ID" in result.output
    assert "NEW_NAME" in result.output


def test_apply_help():
    runner = CliRunner()
    result = runner.invoke(main, ["apply", "--help"])
    assert result.exit_code == 0
    assert "Apply a planned transaction" in result.output
    assert "TX_ID" in result.output


def test_full_rename_workflow(tmp_path):
    project = _make_project(tmp_path, {
        "test.py": "def greet():\n    pass\n\ngreet()\n"
    })
    runner = CliRunner()
    os.chdir(project)

    # Index
    result = runner.invoke(main, ["index", str(project / "test.py")], catch_exceptions=False)
    assert result.exit_code == 0

    # Plan rename
    result = runner.invoke(
        main, ["plan-rename", "test.py:greet", "hello"], catch_exceptions=False
    )
    assert result.exit_code == 0
    output = json.loads(result.output)
    assert "tx_id" in output
    assert output["old_name"] == "greet"
    assert output["new_name"] == "hello"
    assert output["edit_count"] == 2
    tx_id = output["tx_id"]

    # Apply
    result = runner.invoke(main, ["apply", tx_id], catch_exceptions=False)
    assert result.exit_code == 0
    output = json.loads(result.output)
    assert output["status"] == "applied"
    assert "test.py" in output["files_modified"]

    # Verify file changed
    content = (project / "test.py").read_text()
    assert "def hello(" in content
    assert "hello()" in content
    assert "greet" not in content


def test_plan_rename_json_output(tmp_path):
    project = _make_project(tmp_path, {"test.py": "def foo(): pass\n"})
    runner = CliRunner()
    os.chdir(project)

    runner.invoke(main, ["index", str(project / "test.py")], catch_exceptions=False)
    result = runner.invoke(
        main, ["plan-rename", "test.py:foo", "bar"], catch_exceptions=False
    )

    assert result.exit_code == 0
    output = json.loads(result.output)
    assert "tx_id" in output
    assert "operation" in output
    assert output["operation"] == "rename"
    assert "symbol_id" in output
    assert "old_name" in output
    assert "new_name" in output
    assert "files_affected" in output
    assert "edit_count" in output
    assert "created_at" in output


def test_apply_json_output(tmp_path):
    project = _make_project(tmp_path, {"test.py": "def foo(): pass\n"})
    runner = CliRunner()
    os.chdir(project)

    runner.invoke(main, ["index", str(project / "test.py")], catch_exceptions=False)
    plan_result = runner.invoke(
        main, ["plan-rename", "test.py:foo", "bar"], catch_exceptions=False
    )
    tx_id = json.loads(plan_result.output)["tx_id"]

    result = runner.invoke(main, ["apply", tx_id], catch_exceptions=False)
    assert result.exit_code == 0
    output = json.loads(result.output)
    assert "tx_id" in output
    assert "status" in output
    assert output["status"] == "applied"
    assert "files_modified" in output
    assert "files_reindexed" in output


def test_plan_rename_error_not_found(tmp_path):
    project = _make_project(tmp_path, {"test.py": "x = 1\n"})
    runner = CliRunner()
    os.chdir(project)

    runner.invoke(main, ["index", str(project / "test.py")], catch_exceptions=False)
    result = runner.invoke(main, ["plan-rename", "nonexistent", "bar"])

    assert result.exit_code != 0
    output = json.loads(result.output)
    assert "error" in output
    assert "not found" in output["error"]


def test_apply_error_not_found(tmp_path):
    project = _make_project(tmp_path, {})
    runner = CliRunner()
    os.chdir(project)

    result = runner.invoke(main, ["apply", "nonexistent_tx"])
    assert result.exit_code != 0
    output = json.loads(result.output)
    assert "error" in output
    assert "not found" in output["error"]


def test_plan_rename_with_flags(tmp_path):
    project = _make_project(tmp_path, {"test.py": "def foo(): pass\n"})
    runner = CliRunner()
    os.chdir(project)

    runner.invoke(main, ["index", str(project / "test.py")], catch_exceptions=False)
    result = runner.invoke(
        main,
        ["plan-rename", "test.py:foo", "bar", "--include-file", "--include-exports"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    # Flags are recorded but not acted on in v1
    # Just verify the plan succeeds


def test_commands_appear_in_help():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "plan-rename" in result.output
    assert "apply" in result.output
