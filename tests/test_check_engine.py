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


# ── custom rule plugins ─────────────────────────────────────────────────────

import sys as _sys  # noqa: E402
import pytest  # noqa: E402
from pypeeker.check import CheckConfigError, register_rule  # noqa: E402
from pypeeker.check.rules import _REGISTERED, get_rule  # noqa: E402


@pytest.fixture
def clean_registry():
    before = dict(_REGISTERED)
    before_mods = set(_sys.modules)
    yield
    _REGISTERED.clear()
    _REGISTERED.update(before)
    for m in set(_sys.modules) - before_mods:
        _sys.modules.pop(m, None)


def test_register_rule_and_get_rule(clean_registry):
    @register_rule("my-test-rule")
    def _rule(file_index, options):
        return []

    assert get_rule("my-test-rule") is _rule
    # built-ins still resolve and take precedence
    assert get_rule("require-docstrings") is not None


def test_engine_runs_plugin_rule(indexed_project, clean_registry):
    tmp, store = indexed_project({"src/m.py": "def foo():\n    return 1\n"})
    (tmp / "lint_plugin_a.py").write_text(
        "from pypeeker.check import register_rule, Violation\n"
        "\n"
        "@register_rule('no-foo')\n"
        "def no_foo(file_index, options):\n"
        "    out = []\n"
        "    for s in file_index.symbols:\n"
        "        if s.name == 'foo':\n"
        "            out.append(Violation(\n"
        "                file_path=s.location.file_path,\n"
        "                line=s.location.span.start.line + 1,\n"
        "                rule='no-foo', message=\"symbol named 'foo'\"))\n"
        "    return out\n"
    )
    cfg = CheckConfig(src=("src",), rules=("no-foo",), plugins=("lint_plugin_a",))
    violations = CheckEngine(store, cfg).run()
    assert any(v.rule == "no-foo" and "foo" in v.message for v in violations)


def test_engine_plugin_rule_receives_options(indexed_project, clean_registry):
    tmp, store = indexed_project({"src/m.py": "x = 1\n"})
    (tmp / "lint_plugin_b.py").write_text(
        "from pypeeker.check import register_rule, Violation\n"
        "\n"
        "@register_rule('banned-name')\n"
        "def banned(file_index, options):\n"
        "    banned = set(options.get('names', []))\n"
        "    return [Violation(file_path=s.location.file_path,\n"
        "                      line=s.location.span.start.line + 1,\n"
        "                      rule='banned-name', message=s.name)\n"
        "            for s in file_index.symbols if s.name in banned]\n"
    )
    cfg = CheckConfig(
        src=("src",),
        rules=("banned-name",),
        rule_options={"banned-name": {"names": ["x"]}},
        plugins=("lint_plugin_b",),
    )
    violations = CheckEngine(store, cfg).run()
    assert any(v.rule == "banned-name" and v.message == "x" for v in violations)


def test_engine_bad_plugin_errors_clearly(indexed_project, clean_registry):
    _, store = indexed_project({"src/m.py": "x = 1\n"})
    cfg = CheckConfig(src=("src",), rules=("whatever",), plugins=("nope_missing_xyz",))
    with pytest.raises(CheckConfigError, match="nope_missing_xyz"):
        CheckEngine(store, cfg).run()
