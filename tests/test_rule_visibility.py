"""Tests for the builtin visibility-detection rules (check/builtin/visibility).

Three project-scoped rules over one shared usage-scope core:
over-exposed-module-symbol, over-exposed-export, under-exposed-access.
"""

from __future__ import annotations

import pytest

from pypeeker.check.builtin.visibility import (
    OVER_EXPOSED_EXPORT,
    OVER_EXPOSED_MODULE_SYMBOL,
    UNDER_EXPOSED_ACCESS,
    over_exposed_export,
    over_exposed_module_symbol,
    under_exposed_access,
)
from pypeeker.check.context import CheckContext
from pypeeker.check.rules import get_project_rule


@pytest.fixture
def run_rule(indexed_project):
    """Index ``files``, build a CheckContext, run ``rule`` -> violations."""

    def _run(rule, files, options=None):
        _, store = indexed_project(files)
        indexes = [
            idx
            for idx in (store.load(p) for p in store.list_indexed_files())
            if idx is not None
        ]
        context = CheckContext(store, indexes)
        return rule(context, options or {})

    return _run


def test_all_three_rules_registered_as_project_rules():
    # Importing the module above self-registers them via @register_rule.
    for name in (
        OVER_EXPOSED_MODULE_SYMBOL,
        OVER_EXPOSED_EXPORT,
        UNDER_EXPOSED_ACCESS,
    ):
        assert get_project_rule(name) is not None


def test_not_in_default_rules():
    # All three are available but opt-in.
    import tomllib
    from pathlib import Path

    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    enabled = data["tool"]["pypeeker"]["rules"]
    assert OVER_EXPOSED_MODULE_SYMBOL not in enabled
    assert OVER_EXPOSED_EXPORT not in enabled
    assert UNDER_EXPOSED_ACCESS not in enabled


class TestOverExposedModuleSymbol:
    def _msgs(self, run_rule, files, options=None):
        return {
            v.message
            for v in run_rule(over_exposed_module_symbol, files, options)
        }

    def test_flags_module_local_public_function(self, run_rule):
        # helper is public but only referenced within its own module.
        violations = run_rule(
            over_exposed_module_symbol,
            {
                "pkg/lib.py": "def helper():\n    return 1\n\nhelper()\n",
                "pkg/app.py": "x = 1\n",
            },
        )
        flagged = [v for v in violations if "'helper'" in v.message]
        assert len(flagged) == 1
        assert flagged[0].rule == OVER_EXPOSED_MODULE_SYMBOL
        assert "make it _helper" in flagged[0].message
        assert flagged[0].line == 1  # def line, 1-indexed

    def test_unreferenced_public_symbol_also_flagged(self, run_rule):
        # Zero references anywhere: observed scope is still <= its module.
        msgs = self._msgs(
            run_rule, {"pkg/lib.py": "class Orphan:\n    pass\n"}
        )
        assert any("'Orphan'" in m for m in msgs)

    def test_cross_module_use_not_flagged(self, run_rule):
        msgs = self._msgs(
            run_rule,
            {
                "pkg/lib.py": "def helper():\n    return 1\n",
                "pkg/app.py": "from pkg.lib import helper\n\nhelper()\n",
            },
        )
        assert not any("'helper'" in m for m in msgs)

    def test_barrel_exported_symbol_not_flagged(self, run_rule):
        # Re-exported by the package __init__: over-exposed-export's concern.
        msgs = self._msgs(
            run_rule,
            {
                "pkg/lib.py": "class Widget:\n    pass\n",
                "pkg/__init__.py": "from pkg.lib import Widget\n",
            },
        )
        assert not any("'Widget'" in m for m in msgs)

    def test_main_dunder_and_dunder_main_file_exempt(self, run_rule):
        msgs = self._msgs(
            run_rule,
            {
                "pkg/cli.py": (
                    "def main():\n    return 0\n\n"
                    "def __getattr__(name):\n    return 1\n"
                ),
                "pkg/__main__.py": "def entry():\n    return 0\n",
            },
        )
        assert msgs == set()

    def test_allow_decorators_exempts_registry_symbols(self, run_rule):
        files = {
            "pkg/lib.py": (
                "def register(f):\n    return f\n\n"
                "@register\ndef handler():\n    return 1\n"
            ),
        }
        flagged = self._msgs(run_rule, files)
        assert any("'handler'" in m for m in flagged)
        exempt = self._msgs(
            run_rule, files, {"allow-decorators": ["register"]}
        )
        assert not any("'handler'" in m for m in exempt)

    def test_variables_only_checked_when_kinds_opted_in(self, run_rule):
        files = {"pkg/lib.py": "LIMIT = 10\n"}
        assert not any(
            "'LIMIT'" in m for m in self._msgs(run_rule, files)
        )
        msgs = self._msgs(
            run_rule, files, {"kinds": ["function", "class", "variable"]}
        )
        assert any("'LIMIT'" in m for m in msgs)

    def test_allow_pattern_suppresses(self, run_rule):
        msgs = self._msgs(
            run_rule,
            {"pkg/lib.py": "def helper():\n    return 1\n"},
            {"allow": ["pkg.lib:helper"]},
        )
        assert not any("'helper'" in m for m in msgs)


class TestOverExposedExport:
    def _msgs(self, run_rule, files, options=None):
        return {
            v.message for v in run_rule(over_exposed_export, files, options)
        }

    BARREL = {
        "pkg/lib.py": "class Widget:\n    pass\n",
        "pkg/__init__.py": "from pkg.lib import Widget\n",
    }

    def test_flags_unconsumed_export(self, run_rule):
        violations = run_rule(over_exposed_export, dict(self.BARREL))
        flagged = [v for v in violations if "'Widget'" in v.message]
        assert len(flagged) == 1
        assert flagged[0].rule == OVER_EXPOSED_EXPORT
        assert "drop the re-export" in flagged[0].message
        assert flagged[0].file_path.endswith("__init__.py")
        assert flagged[0].line == 1

    def test_outside_consumer_not_flagged(self, run_rule):
        files = dict(self.BARREL)
        files["app.py"] = "from pkg import Widget\n\nw = Widget()\n"
        assert not any(
            "'Widget'" in m for m in self._msgs(run_rule, files)
        )

    def test_intra_package_consumption_still_flagged(self, run_rule):
        # Used, but only from inside the package: the export is still unconsumed.
        files = dict(self.BARREL)
        files["pkg/other.py"] = "from pkg.lib import Widget\n\nw = Widget()\n"
        assert any("'Widget'" in m for m in self._msgs(run_rule, files))

    def test_external_import_in_init_not_flagged(self, run_rule):
        msgs = self._msgs(
            run_rule, {"pkg/__init__.py": "from os import path\n"}
        )
        assert msgs == set()

    def test_allow_matches_export_id(self, run_rule):
        msgs = self._msgs(
            run_rule, dict(self.BARREL), {"allow": ["pkg:Widget"]}
        )
        assert not any("'Widget'" in m for m in msgs)

    def test_allow_matches_canonical_definition_id(self, run_rule):
        msgs = self._msgs(
            run_rule, dict(self.BARREL), {"allow": ["pkg.lib:Widget"]}
        )
        assert not any("'Widget'" in m for m in msgs)


class TestUnderExposedAccess:
    def _msgs(self, run_rule, files, options=None):
        return {
            v.message for v in run_rule(under_exposed_access, files, options)
        }

    def test_flags_cross_module_protected_access(self, run_rule):
        violations = run_rule(
            under_exposed_access,
            {
                "pkg/lib.py": "def _secret():\n    return 1\n",
                "pkg/app.py": "from pkg.lib import _secret\n\n_secret()\n",
            },
        )
        flagged = [v for v in violations if "'_secret'" in v.message]
        assert flagged, "expected a violation for the cross-module reach-in"
        v = flagged[0]
        assert v.rule == UNDER_EXPOSED_ACCESS
        assert v.message.startswith("protected '_secret'")
        assert "outside its defining module" in v.message
        assert "accessed from tests" not in v.message
        assert v.file_path == "pkg/app.py"
        assert v.line == 3  # the call site, 1-indexed

    def test_test_origin_reported_distinctly(self, run_rule):
        msgs = self._msgs(
            run_rule,
            {
                "pkg/lib.py": "def _secret():\n    return 1\n",
                "tests/test_app.py": (
                    "from pkg.lib import _secret\n\n_secret()\n"
                ),
            },
        )
        assert any(
            "'_secret'" in m and "accessed from tests" in m for m in msgs
        )

    def test_same_module_use_not_flagged(self, run_rule):
        msgs = self._msgs(
            run_rule,
            {"pkg/lib.py": "def _secret():\n    return 1\n\n_secret()\n"},
        )
        assert not any("'_secret'" in m for m in msgs)

    def test_dunder_access_not_flagged(self, run_rule):
        msgs = self._msgs(
            run_rule,
            {
                "pkg/lib.py": '__version__ = "1"\n',
                "pkg/app.py": (
                    "from pkg.lib import __version__\n\nv = __version__\n"
                ),
            },
        )
        assert not any("'__version__'" in m for m in msgs)

    def test_private_double_underscore_flagged(self, run_rule):
        msgs = self._msgs(
            run_rule,
            {
                "pkg/lib.py": "def __hidden():\n    return 1\n",
                "pkg/app.py": "from pkg.lib import __hidden\n\n__hidden()\n",
            },
        )
        assert any(m.startswith("private '__hidden'") for m in msgs)

    def test_allow_pattern_suppresses(self, run_rule):
        msgs = self._msgs(
            run_rule,
            {
                "pkg/lib.py": "def _secret():\n    return 1\n",
                "pkg/app.py": "from pkg.lib import _secret\n\n_secret()\n",
            },
            {"allow": ["pkg.lib:_secret"]},
        )
        assert not any("'_secret'" in m for m in msgs)

    def test_custom_test_globs_classify_origin(self, run_rule):
        msgs = self._msgs(
            run_rule,
            {
                "pkg/lib.py": "def _secret():\n    return 1\n",
                "checks/check_app.py": (
                    "from pkg.lib import _secret\n\n_secret()\n"
                ),
            },
            {"test-globs": ["checks/*"]},
        )
        assert any(
            "'_secret'" in m and "accessed from tests" in m for m in msgs
        )
