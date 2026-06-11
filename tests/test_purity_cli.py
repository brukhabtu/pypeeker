"""Tests for the `pypeeker purity` CLI command."""

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


def _index(runner: CliRunner, project: Path, name: str = "test.py") -> None:
    result = runner.invoke(
        main, ["index", str(project / name)], catch_exceptions=False
    )
    assert result.exit_code == 0


def test_purity_help():
    runner = CliRunner()
    result = runner.invoke(main, ["purity", "--help"])
    assert result.exit_code == 0
    assert "SYMBOL_ID" in result.output
    assert "purity verdict" in result.output
    assert "--no-refresh" in result.output


def test_purity_pure_function(tmp_path):
    project = _make_project(tmp_path, {
        "test.py": "def add(a, b):\n    return a + b\n"
    })
    runner = CliRunner()
    os.chdir(project)
    _index(runner, project)

    result = runner.invoke(main, ["purity", "test:add"], catch_exceptions=False)
    assert result.exit_code == 0
    output = json.loads(result.output)
    assert output["symbol_id"] == "test:add"
    assert output["pure"] is True
    assert output["observations"] == []


def test_purity_impure_function_observation_payload(tmp_path):
    project = _make_project(tmp_path, {
        "test.py": "def shout(msg):\n    print(msg)\n    return msg\n"
    })
    runner = CliRunner()
    os.chdir(project)
    _index(runner, project)

    result = runner.invoke(main, ["purity", "test:shout"], catch_exceptions=False)
    assert result.exit_code == 0
    output = json.loads(result.output)
    assert output["symbol_id"] == "test:shout"
    assert output["pure"] is False
    assert len(output["observations"]) == 1
    obs = output["observations"][0]
    assert obs["kind"] == "BareCall"
    assert obs["name"] == "print"
    assert obs["line"] == 1


def test_purity_transitive_impure_call(tmp_path):
    project = _make_project(tmp_path, {
        "test.py": (
            "def helper(x):\n"
            "    print(x)\n"
            "\n"
            "def caller(x):\n"
            "    return helper(x)\n"
        )
    })
    runner = CliRunner()
    os.chdir(project)
    _index(runner, project)

    result = runner.invoke(main, ["purity", "test:caller"], catch_exceptions=False)
    assert result.exit_code == 0
    output = json.loads(result.output)
    assert output["pure"] is False
    kinds = {o["kind"] for o in output["observations"]}
    assert "TransitiveImpureCall" in kinds
    transitive = [
        o for o in output["observations"] if o["kind"] == "TransitiveImpureCall"
    ]
    assert transitive[0]["callee"] == "test:helper"


def test_purity_not_found_error(tmp_path):
    project = _make_project(tmp_path, {"test.py": "def f():\n    pass\n"})
    runner = CliRunner()
    os.chdir(project)
    _index(runner, project)

    result = runner.invoke(main, ["purity", "test:nonexistent"])
    assert result.exit_code != 0
    output = json.loads(result.output)
    assert "error" in output
    assert output["reason"] == "not_found"


def test_purity_not_a_function_error(tmp_path):
    project = _make_project(tmp_path, {
        "test.py": "class Thing:\n    pass\n"
    })
    runner = CliRunner()
    os.chdir(project)
    _index(runner, project)

    result = runner.invoke(main, ["purity", "test:Thing"])
    assert result.exit_code != 0
    output = json.loads(result.output)
    assert "error" in output
    assert output["reason"] == "not_a_function"
    assert output["symbol_id"] == "test:Thing"


def test_purity_appears_in_help():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "purity" in result.output
