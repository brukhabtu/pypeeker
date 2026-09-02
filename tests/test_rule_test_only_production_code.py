"""Tests for the test-only-production-code project rule (check/builtin)."""

from __future__ import annotations

# Aliased on import: the rule's name starts with ``test_`` and pytest would
# otherwise collect it as a test function.
from pypeeker.check.builtin.test_only_production_code import (
    TEST_ONLY_PRODUCTION_CODE,
    _test_only_production_code as rule,
)
from pypeeker.check.rules import get_project_rule

PROD = "def helper():\n    return 1\n"
TEST_USE = "from pkg.lib import helper\n\nhelper()\n"


class TestTestOnlyProductionCode:
    def _flagged(self, run_rule, files, options=None):
        return {v.message for v in run_rule(rule, files, options)}

    def test_used_only_by_tests_is_flagged(self, run_rule):
        msgs = self._flagged(
            run_rule,
            {"pkg/lib.py": PROD, "tests/test_lib.py": TEST_USE},
        )
        assert any(":helper'" in m and "only from tests" in m for m in msgs)

    def test_used_by_prod_and_tests_not_flagged(self, run_rule):
        msgs = self._flagged(
            run_rule,
            {
                "pkg/lib.py": PROD,
                "pkg/app.py": "from pkg.lib import helper\n\nhelper()\n",
                "tests/test_lib.py": TEST_USE,
            },
        )
        assert not any(":helper'" in m for m in msgs)

    def test_same_module_use_counts_as_production(self, run_rule):
        msgs = self._flagged(
            run_rule,
            {
                "pkg/lib.py": "def helper():\n    return 1\n\nhelper()\n",
                "tests/test_lib.py": TEST_USE,
            },
        )
        assert not any(":helper'" in m for m in msgs)

    def test_zero_references_not_flagged(self, run_rule):
        # No references anywhere: that is unused-public-symbol's job.
        msgs = self._flagged(run_rule, {"pkg/lib.py": PROD})
        assert msgs == set()

    def test_barrel_reexported_excluded(self, run_rule):
        # Barrel re-export = deliberate API surface, excluded even when the
        # only in-repo references are tests.
        msgs = self._flagged(
            run_rule,
            {
                "pkg/lib.py": PROD,
                "pkg/__init__.py": "from pkg.lib import helper\n",
                "tests/test_lib.py": TEST_USE,
            },
        )
        assert not any(":helper'" in m for m in msgs)

    def test_helper_defined_in_test_file_not_flagged(self, run_rule):
        # Only non-test definitions are in scope.
        msgs = self._flagged(
            run_rule,
            {
                "tests/helpers.py": "def make_thing():\n    return 1\n",
                "tests/test_lib.py": (
                    "from tests.helpers import make_thing\n\nmake_thing()\n"
                ),
            },
        )
        assert msgs == set()

    def test_test_prefixed_module_at_any_depth_is_test(self, run_rule):
        # Default globs cover test_*.py outside a tests/ directory too.
        msgs = self._flagged(
            run_rule,
            {"pkg/lib.py": PROD, "pkg/sub/test_lib.py": TEST_USE},
        )
        assert any(":helper'" in m for m in msgs)

    def test_custom_test_globs(self, run_rule):
        files = {"pkg/lib.py": PROD, "checks/check_lib.py": TEST_USE}
        # Default globs: checks/ is production, so helper has a prod reference.
        assert not any(":helper'" in m for m in self._flagged(run_rule, files))
        # Custom globs reclassify checks/ as tests; defaults are replaced.
        msgs = self._flagged(
            run_rule, files, {"test-globs": ["checks/**"]}
        )
        assert any(":helper'" in m for m in msgs)

    def test_allow_suppresses_symbol(self, run_rule):
        files = {"pkg/lib.py": PROD, "tests/test_lib.py": TEST_USE}
        msgs = self._flagged(run_rule, files, {"allow": ["pkg.lib:helper"]})
        assert not any(":helper'" in m for m in msgs)
        # Module-path patterns work too.
        msgs = self._flagged(run_rule, files, {"allow": ["pkg.*"]})
        assert not any(":helper'" in m for m in msgs)

    def test_private_and_nested_symbols_skipped(self, run_rule):
        msgs = self._flagged(
            run_rule,
            {
                "pkg/lib.py": (
                    "def _hidden():\n    return 1\n\n"
                    "class Widget:\n    def method(self):\n        return 2\n"
                ),
                "tests/test_lib.py": (
                    "from pkg.lib import _hidden, Widget\n\n"
                    "_hidden()\nWidget().method()\n"
                ),
            },
        )
        # _hidden is private and method is not module-level; Widget IS flagged.
        assert not any("'_hidden'" in m for m in msgs)
        assert not any(":method'" in m for m in msgs)
        assert any(":Widget'" in m for m in msgs)

    def test_violation_shape_line_1_indexed(self, run_rule):
        violations = run_rule(
            rule,
            {
                "pkg/lib.py": "\ndef helper():\n    return 1\n",
                "tests/test_lib.py": TEST_USE,
            },
        )
        assert len(violations) == 1
        v = violations[0]
        assert v.rule == TEST_ONLY_PRODUCTION_CODE
        assert v.file_path == "pkg/lib.py"
        assert v.line == 2  # def line, 1-indexed
        assert v.message == "'pkg.lib:helper' is referenced only from tests (1 test reference)"

    def test_registered_as_project_rule(self):
        assert get_project_rule(TEST_ONLY_PRODUCTION_CODE) is rule

    def test_enabled_as_self_lint_gate(self):
        # Enabled on pypeeker itself as a gate (TASK-112). Anchored to this
        # file (not cwd) because other test modules chdir without restoring.
        import tomllib
        from pathlib import Path

        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text())
        assert TEST_ONLY_PRODUCTION_CODE in data["tool"]["pypeeker"]["rules"]
