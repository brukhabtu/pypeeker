"""Tests for CLI commands."""

from __future__ import annotations

import json
import os
from pathlib import Path

from click.testing import CliRunner

from pypeeker.cli import main


def _make_project(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create a project directory with source files and pyproject.toml."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return tmp_path


def test_index_single_file(tmp_path):
    project = _make_project(tmp_path, {"hello.py": "def greet(): pass\n"})
    runner = CliRunner()
    result = runner.invoke(main, ["index", str(project / "hello.py")], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    output = json.loads(result.output)
    assert len(output["indexed"]) == 1
    assert "hello.py" in output["indexed"][0]


def test_index_directory(tmp_path):
    project = _make_project(
        tmp_path,
        {
            "src/a.py": "x = 1\n",
            "src/b.py": "y = 2\n",
        },
    )
    runner = CliRunner()
    result = runner.invoke(main, ["index", str(project / "src")], catch_exceptions=False)
    assert result.exit_code == 0
    output = json.loads(result.output)
    assert len(output["indexed"]) == 2


def test_index_skips_unchanged(tmp_path):
    project = _make_project(tmp_path, {"test.py": "x = 1\n"})
    runner = CliRunner()
    # First index
    runner.invoke(main, ["index", str(project / "test.py")], catch_exceptions=False)
    # Second index should skip
    result = runner.invoke(main, ["index", str(project / "test.py")], catch_exceptions=False)
    output = json.loads(result.output)
    assert len(output["skipped"]) == 1
    assert len(output["indexed"]) == 0


def test_symbol_lookup(tmp_path):
    project = _make_project(tmp_path, {"test.py": "def greet(): pass\n"})
    runner = CliRunner()
    os.chdir(project)
    runner.invoke(main, ["index", str(project / "test.py")], catch_exceptions=False)
    result = runner.invoke(main, ["symbol", "greet"], catch_exceptions=False)
    assert result.exit_code == 0
    output = json.loads(result.output)
    assert len(output) == 1
    assert output[0]["name"] == "greet"


def test_refs_command(tmp_path):
    project = _make_project(tmp_path, {"test.py": "def greet(): pass\ngreet()\n"})
    runner = CliRunner()
    os.chdir(project)
    runner.invoke(main, ["index", str(project / "test.py")], catch_exceptions=False)
    result = runner.invoke(main, ["refs", "test.py:greet"], catch_exceptions=False)
    assert result.exit_code == 0
    output = json.loads(result.output)
    assert len(output) >= 1


def test_scope_command(tmp_path):
    project = _make_project(
        tmp_path, {"test.py": "x = 1\ndef foo():\n    y = 2\n"}
    )
    runner = CliRunner()
    os.chdir(project)
    runner.invoke(main, ["index", str(project / "test.py")], catch_exceptions=False)
    result = runner.invoke(main, ["scope", "test.py:2"], catch_exceptions=False)
    assert result.exit_code == 0
    output = json.loads(result.output)
    assert "scope" in output
    assert output["scope"]["name"] == "foo"


def test_index_nonexistent_path(tmp_path):
    _make_project(tmp_path, {})
    runner = CliRunner()
    result = runner.invoke(main, ["index", str(tmp_path / "nonexistent")])
    assert result.exit_code != 0


def test_help():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "pypeeker" in result.output
    assert "index" in result.output
    assert "symbol" in result.output
    assert "refs" in result.output
    assert "scope" in result.output
