"""Tests for the CheckEngine and the `pypeeker check` CLI."""

from __future__ import annotations

from click.testing import CliRunner

from pypeeker.check import CheckEngine
from pypeeker.check.config import CheckConfig
from pypeeker.cli import main


def test_engine_returns_empty_when_no_rules(indexed_project):
    _, store = indexed_project({"src/m.py": "def foo():\n    return 1\n"})
    engine = CheckEngine(store, CheckConfig(src=("src",), rules=()))
    assert engine.run() == []


def test_engine_runs_require_docstrings(indexed_project):
    _, store = indexed_project({"src/m.py": "def foo():\n    return 1\n"})
    cfg = CheckConfig(src=("src",), rules=("require-docstrings",))
    violations = CheckEngine(store, cfg).run()
    assert any(
        v.rule == "require-docstrings" and "foo" in v.message for v in violations
    )


def test_engine_respects_src_filter(indexed_project):
    files = {
        "src/m.py": "def foo():\n    return 1\n",
        "tests/m.py": "def bar():\n    return 1\n",
    }
    _, store = indexed_project(files)
    cfg = CheckConfig(src=("src",), rules=("require-docstrings",))
    violations = CheckEngine(store, cfg).run()
    assert any("foo" in v.message for v in violations)
    assert not any("bar" in v.message for v in violations)


def test_engine_sorts_by_file_then_line(indexed_project):
    files = {
        "src/b.py": "def b():\n    return 1\n",
        "src/a.py": "\ndef a():\n    return 1\n",
    }
    _, store = indexed_project(files)
    cfg = CheckConfig(src=("src",), rules=("require-docstrings",))
    violations = CheckEngine(store, cfg).run()
    paths = [v.file_path for v in violations]
    assert paths == sorted(paths)


def test_engine_passes_options_per_rule(indexed_project):
    _, store = indexed_project({"src/m.py": "def _h():\n    return 1\n"})
    cfg = CheckConfig(
        src=("src",),
        rules=("require-docstrings",),
        rule_options={"require-docstrings": {"visibility": ["protected"]}},
    )
    violations = CheckEngine(store, cfg).run()
    assert any("_h" in v.message for v in violations)


def test_engine_ignores_unknown_rule_names(indexed_project):
    _, store = indexed_project({"src/m.py": "def foo():\n    return 1\n"})
    cfg = CheckConfig(src=("src",), rules=("does-not-exist",))
    assert CheckEngine(store, cfg).run() == []


def test_check_cli_exits_nonzero_on_violations(monkeypatch, indexed_project):
    project_dir, _ = indexed_project({"src/m.py": "def foo():\n    return 1\n"})
    (project_dir / "pyproject.toml").write_text(
        "[tool.pypeeker]\n"
        'src = ["src"]\n'
        'rules = ["require-docstrings"]\n'
    )
    monkeypatch.chdir(project_dir)
    result = CliRunner().invoke(main, ["check"])
    assert result.exit_code == 1
    assert "src/m.py:" in result.output
    assert "[require-docstrings]" in result.output


def test_check_cli_exits_zero_with_no_violations(monkeypatch, indexed_project):
    project_dir, _ = indexed_project(
        {"src/m.py": 'def foo():\n    """ok"""\n    return 1\n'}
    )
    (project_dir / "pyproject.toml").write_text(
        "[tool.pypeeker]\n"
        'src = ["src"]\n'
        'rules = ["require-docstrings"]\n'
    )
    monkeypatch.chdir(project_dir)
    result = CliRunner().invoke(main, ["check"])
    assert result.exit_code == 0
    assert result.output == ""
