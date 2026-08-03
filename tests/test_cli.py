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
    result = runner.invoke(main, ["refs", "test:greet"], catch_exceptions=False)
    assert result.exit_code == 0
    output = json.loads(result.output)
    assert len(output) >= 1


def test_refs_all_includes_resolution_field(tmp_path):
    project = _make_project(
        tmp_path,
        {
            "lib.py": "class Svc:\n    def run(self):\n        return 1\n",
            "app.py": (
                "from lib import Svc\n\n"
                "def declared(s: Svc):\n    return s.run()\n\n"
                "def inferred():\n    s = Svc()\n    return s.run()\n"
            ),
        },
    )
    runner = CliRunner()
    os.chdir(project)
    runner.invoke(main, ["index", str(project)], catch_exceptions=False)
    result = runner.invoke(
        main, ["refs", "lib:Svc.run", "--all"], catch_exceptions=False
    )
    assert result.exit_code == 0
    output = json.loads(result.output)
    assert output
    assert all("resolution" in item for item in output)
    by_scope = {item["in_scope_id"]: item["resolution"] for item in output}
    assert by_scope["app:declared"] == "receiver_declared"
    assert by_scope["app:inferred"] == "receiver_inferred"

    # Without --all the JSON shape is unchanged (no resolution field).
    result = runner.invoke(
        main, ["refs", "lib:Svc.run"], catch_exceptions=False
    )
    assert result.exit_code == 0
    plain = json.loads(result.output)
    assert all("resolution" not in item for item in plain)


def test_refs_all_resolution_kinds_for_imports(tmp_path):
    project = _make_project(
        tmp_path,
        {
            "pkg/lib.py": "class Widget:\n    pass\n\nWidget()\n",
            "pkg/__init__.py": "from pkg.lib import Widget\n",
            "pkg/app.py": "from pkg import Widget\nWidget()\n",
            "pkg/direct.py": "from pkg.lib import Widget\nWidget()\n",
        },
    )
    runner = CliRunner()
    os.chdir(project)
    runner.invoke(main, ["index", str(project)], catch_exceptions=False)
    result = runner.invoke(
        main, ["refs", "pkg.lib:Widget", "--all"], catch_exceptions=False
    )
    assert result.exit_code == 0
    output = json.loads(result.output)
    by_file = {
        item["location"]["file_path"]: item["resolution"] for item in output
    }
    assert by_file["pkg/lib.py"] == "direct"
    assert by_file["pkg/app.py"] == "barrel"
    assert by_file["pkg/direct.py"] == "import_alias"


def test_refs_help_documents_resolution_values(tmp_path):
    runner = CliRunner()
    result = runner.invoke(main, ["refs", "--help"])
    assert result.exit_code == 0
    for kind in (
        "direct",
        "import_alias",
        "barrel",
        "receiver_declared",
        "receiver_inferred",
    ):
        assert kind in result.output


def test_refs_refuses_unresolved_symbol_id(tmp_path):
    # TASK-150: [] exit 0 for an id that names nothing is indistinguishable
    # from a real zero-reference answer. It must refuse instead.
    project = _make_project(tmp_path, {"test.py": "def greet(): pass\ngreet()\n"})
    runner = CliRunner()
    os.chdir(project)
    runner.invoke(main, ["index", str(project / "test.py")], catch_exceptions=False)
    result = runner.invoke(main, ["refs", "totally.made.up:Nonexistent"])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["code"] == "unresolved-symbol"
    assert "totally.made.up:Nonexistent" in payload["error"]
    assert payload["symbol_id"] == "totally.made.up:Nonexistent"
    assert "candidates" not in payload


def test_refs_refuses_file_path_form_and_suggests_canonical_id(tmp_path):
    project = _make_project(tmp_path, {"test.py": "def greet(): pass\ngreet()\n"})
    runner = CliRunner()
    os.chdir(project)
    runner.invoke(main, ["index", str(project / "test.py")], catch_exceptions=False)
    result = runner.invoke(main, ["refs", "test.py:greet"])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["code"] == "unresolved-symbol"
    assert "test:greet" in payload["candidates"]


def test_refs_zero_references_on_resolved_symbol_is_empty_and_ok(tmp_path):
    # The guard must not over-refuse: a symbol that exists but is never used
    # is a true empty answer.
    project = _make_project(tmp_path, {"test.py": "def unused(): pass\n"})
    runner = CliRunner()
    os.chdir(project)
    runner.invoke(main, ["index", str(project / "test.py")], catch_exceptions=False)
    result = runner.invoke(main, ["refs", "test:unused"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == []


def test_refs_on_a_builtin_binding_without_a_symbol_record_still_returns_refs(tmp_path):
    # Load-bearing ordering: the guard runs only on an EMPTY result. A builtin
    # binding carries references but has no Symbol of its own, so a pre-check
    # would refuse a query that legitimately has data.
    project = _make_project(
        tmp_path, {"test.py": "def pick(obj):\n    return getattr(obj, 'x')\n"}
    )
    runner = CliRunner()
    os.chdir(project)
    runner.invoke(main, ["index", str(project / "test.py")], catch_exceptions=False)
    result = runner.invoke(main, ["refs", "<builtins>.getattr"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert len(json.loads(result.output)) >= 1


def test_refs_all_refuses_unresolved_symbol_id(tmp_path):
    project = _make_project(tmp_path, {"test.py": "def greet(): pass\ngreet()\n"})
    runner = CliRunner()
    os.chdir(project)
    runner.invoke(main, ["index", str(project / "test.py")], catch_exceptions=False)
    result = runner.invoke(main, ["refs", "made.up:Ghost", "--all"])
    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["code"] == "unresolved-symbol"


def test_tree_refuses_unresolved_node(tmp_path):
    project = _make_project(tmp_path, {"test.py": "def greet(): pass\n"})
    runner = CliRunner()
    os.chdir(project)
    runner.invoke(main, ["index", str(project / "test.py")], catch_exceptions=False)
    result = runner.invoke(main, ["tree", "made.up.module"])
    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["code"] == "unresolved-symbol"


def test_tree_resolved_node_without_members_is_empty_and_ok(tmp_path):
    project = _make_project(tmp_path, {"test.py": "def greet(): pass\n"})
    runner = CliRunner()
    os.chdir(project)
    runner.invoke(main, ["index", str(project / "test.py")], catch_exceptions=False)
    result = runner.invoke(main, ["tree", "test:greet"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == []


def test_symbol_lookup_unknown_name_is_empty_not_an_error(tmp_path):
    # 'symbol' is a search, not an id lookup: [] means "nothing matched",
    # which is an honest answer with no second reading. Swept and left alone.
    project = _make_project(tmp_path, {"test.py": "def greet(): pass\n"})
    runner = CliRunner()
    os.chdir(project)
    runner.invoke(main, ["index", str(project / "test.py")], catch_exceptions=False)
    result = runner.invoke(main, ["symbol", "NoSuchThingAtAll"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == []


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


def test_scope_command_error_exits_nonzero(tmp_path):
    # An engine-level error (here: a line with no scope in an indexed file)
    # is emitted as an {"error": ...} payload and must exit non-zero, like
    # every other error path — not signal success with exit 0.
    project = _make_project(
        tmp_path, {"test.py": "x = 1\ndef foo():\n    y = 2\n"}
    )
    runner = CliRunner()
    os.chdir(project)
    runner.invoke(main, ["index", str(project / "test.py")], catch_exceptions=False)
    result = runner.invoke(main, ["scope", "test.py:999"], catch_exceptions=False)
    assert result.exit_code == 1
    output = json.loads(result.output)
    assert "error" in output


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
