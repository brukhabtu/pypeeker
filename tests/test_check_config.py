"""Tests for pypeeker.check.config."""

from __future__ import annotations

from pypeeker.check.config import DEFAULT_SRC, CheckConfig, load_config
from pypeeker.project import DEFAULT_SRC_ROOTS


def test_default_src_is_shared_with_project_module():
    assert DEFAULT_SRC is DEFAULT_SRC_ROOTS


def test_missing_pyproject_returns_defaults(tmp_path):
    cfg = load_config(tmp_path)
    assert cfg == CheckConfig()
    assert cfg.rules == ()
    assert cfg.src == ("src",)


def test_pyproject_without_pypeeker_section_returns_defaults(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    cfg = load_config(tmp_path)
    assert cfg.rules == ()
    assert cfg.src == ("src",)


def test_parses_rules_and_src(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pypeeker]\n"
        'src = ["src", "tests"]\n'
        'rules = ["require-docstrings"]\n'
    )
    cfg = load_config(tmp_path)
    assert cfg.src == ("src", "tests")
    assert cfg.rules == ("require-docstrings",)


def test_parses_rule_options(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pypeeker]\n"
        'rules = ["require-docstrings"]\n'
        "\n"
        "[tool.pypeeker.require-docstrings]\n"
        'kinds = ["function"]\n'
        'visibility = ["public", "protected"]\n'
    )
    cfg = load_config(tmp_path)
    assert cfg.rule_options["require-docstrings"] == {
        "kinds": ["function"],
        "visibility": ["public", "protected"],
    }
