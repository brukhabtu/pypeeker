"""Tests for the automatic index refresh performed by query/check/plan commands."""

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


def _indexed_project(tmp_path: Path, files: dict[str, str], runner: CliRunner) -> Path:
    project = _make_project(tmp_path, files)
    os.chdir(project)
    for name in files:
        result = runner.invoke(
            main, ["index", str(project / name)], catch_exceptions=False
        )
        assert result.exit_code == 0, result.output
    return project


def test_refs_sees_edits_made_after_indexing(tmp_path):
    runner = CliRunner()
    project = _indexed_project(
        tmp_path, {"test.py": "def greet(): pass\ngreet()\n"}, runner
    )

    # Edit on disk without re-indexing: an extra call site appears.
    (project / "test.py").write_text("def greet(): pass\ngreet()\ngreet()\n")

    result = runner.invoke(main, ["refs", "test:greet"], catch_exceptions=False)
    assert result.exit_code == 0
    output = json.loads(result.output)
    assert len(output) == 2


def test_refs_no_refresh_serves_stale_index(tmp_path):
    runner = CliRunner()
    project = _indexed_project(
        tmp_path, {"test.py": "def greet(): pass\ngreet()\n"}, runner
    )

    (project / "test.py").write_text("def greet(): pass\ngreet()\ngreet()\n")

    result = runner.invoke(
        main, ["refs", "--no-refresh", "test:greet"], catch_exceptions=False
    )
    assert result.exit_code == 0
    output = json.loads(result.output)
    assert len(output) == 1  # old fast path: stale answer


def test_symbol_sees_renamed_definition(tmp_path):
    runner = CliRunner()
    project = _indexed_project(tmp_path, {"test.py": "def greet(): pass\n"}, runner)

    (project / "test.py").write_text("def hello(): pass\n")

    result = runner.invoke(main, ["symbol", "hello"], catch_exceptions=False)
    assert result.exit_code == 0
    output = json.loads(result.output)
    assert len(output) == 1
    assert output[0]["name"] == "hello"

    # The old name is gone from the refreshed index.
    result = runner.invoke(main, ["symbol", "greet"], catch_exceptions=False)
    assert json.loads(result.output) == []


def test_check_runs_against_refreshed_index(tmp_path):
    runner = CliRunner()
    project = _indexed_project(tmp_path, {"src/m.py": "x = 1\n"}, runner)
    (project / "pyproject.toml").write_text(
        '[project]\nname = "test"\n'
        "[tool.pypeeker]\n"
        'src = ["src"]\n'
        'rules = ["require-docstrings"]\n'
    )

    # Introduce a violation after indexing; check must re-index to see it.
    (project / "src" / "m.py").write_text("def foo():\n    return 1\n")

    stale = runner.invoke(main, ["check", "--no-refresh"], catch_exceptions=False)
    assert stale.exit_code == 0  # old fast path: stale index hides the violation

    fresh = runner.invoke(main, ["check"], catch_exceptions=False)
    assert fresh.exit_code == 1
    assert "[require-docstrings]" in fresh.output


def test_query_on_never_indexed_project_stays_empty(tmp_path):
    runner = CliRunner()
    project = _make_project(tmp_path, {"test.py": "def greet(): pass\n"})
    os.chdir(project)

    result = runner.invoke(main, ["symbol", "greet"], catch_exceptions=False)
    assert result.exit_code == 0
    assert json.loads(result.output) == []
    # The refresh must not have silently indexed the project.
    index_dir = project / ".semantic-tool" / "index"
    assert not index_dir.exists() or not list(index_dir.rglob("*.json"))


def test_refresh_prunes_deleted_files(tmp_path):
    runner = CliRunner()
    project = _indexed_project(
        tmp_path,
        {"keep.py": "def kept(): pass\n", "gone.py": "def lost(): pass\n"},
        runner,
    )

    (project / "gone.py").unlink()

    result = runner.invoke(main, ["symbol", "lost"], catch_exceptions=False)
    assert result.exit_code == 0
    assert json.loads(result.output) == []
    assert not (
        project / ".semantic-tool" / "index" / "gone.py.json"
    ).exists()


def test_plan_extract_variable_refreshes_stale_index(tmp_path):
    runner = CliRunner()
    project = _indexed_project(
        tmp_path, {"m.py": "def f():\n    return 1\n"}, runner
    )

    # New content on disk; the index is stale until the command refreshes it.
    (project / "m.py").write_text("def f():\n    return foo(bar) + 2\n")

    result = runner.invoke(
        main,
        ["plan-extract-variable", "m.py", "1:11", "1:19", "value"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    output = json.loads(result.output)
    assert output["new_name"] == "value"


def test_plan_extract_variable_no_refresh_refuses_stale(tmp_path):
    runner = CliRunner()
    project = _indexed_project(
        tmp_path, {"m.py": "def f():\n    return 1\n"}, runner
    )

    (project / "m.py").write_text("def f():\n    return foo(bar) + 2\n")

    result = runner.invoke(
        main,
        ["plan-extract-variable", "--no-refresh", "m.py", "1:11", "1:19", "value"],
        catch_exceptions=False,
    )
    assert result.exit_code == 1
    assert "stale" in json.loads(result.output)["error"]


def test_plan_extract_method_no_refresh_refuses_stale(tmp_path):
    runner = CliRunner()
    project = _indexed_project(
        tmp_path, {"m.py": "def f(a, b):\n    return a\n"}, runner
    )

    (project / "m.py").write_text("def f(a, b):\n    c = a + b\n    return c\n")

    result = runner.invoke(
        main,
        ["plan-extract-method", "--no-refresh", "m.py", "1", "1", "add"],
        catch_exceptions=False,
    )
    assert result.exit_code == 1
    assert "stale" in json.loads(result.output)["error"]


def test_plan_inline_variable_refreshes_stale_index(tmp_path):
    runner = CliRunner()
    project = _indexed_project(
        tmp_path, {"m.py": "def f():\n    return 1\n"}, runner
    )

    (project / "m.py").write_text("def f():\n    x = 1\n    return x\n")

    result = runner.invoke(
        main, ["plan-inline-variable", "m:f:x"], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    output = json.loads(result.output)
    assert output["operation"] == "inline_variable"
