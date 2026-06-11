"""Tests for pypeeker.project."""

from __future__ import annotations

from pypeeker.project import (
    DEFAULT_SRC_ROOTS,
    load_pypeeker_section,
    load_src_roots,
)


def test_load_section_missing_pyproject_returns_empty(tmp_path):
    assert load_pypeeker_section(tmp_path) == {}


def test_load_section_without_pypeeker_table_returns_empty(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    assert load_pypeeker_section(tmp_path) == {}


def test_load_section_returns_raw_table(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pypeeker]\n"
        'src = ["src", "lib"]\n'
        'rules = ["require-docstrings"]\n'
        "\n"
        "[tool.pypeeker.require-docstrings]\n"
        'kinds = ["function"]\n'
    )
    section = load_pypeeker_section(tmp_path)
    assert section["src"] == ["src", "lib"]
    assert section["rules"] == ["require-docstrings"]
    assert section["require-docstrings"] == {"kinds": ["function"]}


def test_load_src_roots_defaults_when_absent(tmp_path):
    assert load_src_roots(tmp_path) == DEFAULT_SRC_ROOTS
    (tmp_path / "pyproject.toml").write_text("[tool.pypeeker]\nrules = []\n")
    assert load_src_roots(tmp_path) == DEFAULT_SRC_ROOTS


def test_load_src_roots_reads_configured_value(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pypeeker]\nsrc = ["src", "tests"]\n'
    )
    assert load_src_roots(tmp_path) == ("src", "tests")
