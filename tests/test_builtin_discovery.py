"""Tests for the auto-discovered builtin rule package (check/builtin)."""

from __future__ import annotations

import sys
import textwrap

from pypeeker.check.builtin import _import_submodules as import_submodules
from pypeeker.check.rules import get_rule


def test_import_submodules_imports_and_registers(tmp_path, monkeypatch):
    """A rule module dropped into a package is imported and self-registers."""
    pkg = tmp_path / "fake_builtin"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "myrule.py").write_text(
        textwrap.dedent(
            """
            from pypeeker.check.rules import register_rule

            @register_rule("discovery-smoke-rule")
            def rule(file_index, options):
                return []
            """
        )
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    import importlib

    package = importlib.import_module("fake_builtin")
    try:
        imported = import_submodules(package)
        assert imported == ["myrule"]
        assert get_rule("discovery-smoke-rule") is not None
    finally:
        from pypeeker.check import rules

        rules._REGISTERED.pop("discovery-smoke-rule", None)
        sys.modules.pop("fake_builtin.myrule", None)
        sys.modules.pop("fake_builtin", None)


def test_builtin_package_imports_cleanly():
    """The real builtin package imports without error (currently empty)."""
    import pypeeker.check.builtin  # noqa: F401
