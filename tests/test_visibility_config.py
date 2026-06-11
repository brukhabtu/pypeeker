"""Tests for [tool.pypeeker.visibility]: library mode, public roots,
global decorator allowlists, and the dynamic-access proximity heuristic.

Covers the parsing layer (pypeeker.project), the injection mechanism
(check.config puts the raw table into every enabled rule's options under the
reserved "visibility" key), and consumption by the four dead-code /
demotion rules: unused-public-symbol, over-exposed-module-symbol,
over-exposed-export, test-only-production-code.
"""

from __future__ import annotations

import pytest

from pypeeker.check import CheckContext
from pypeeker.check.builtin.test_only_production_code import (
    test_only_production_code as only_from_tests_rule,
)
from pypeeker.check.builtin.visibility import (
    over_exposed_export,
    over_exposed_module_symbol,
)
from pypeeker.check.config import CheckConfig, load_config
from pypeeker.check.rules import unused_public_symbol
from pypeeker.project import (
    VisibilityConfig,
    coerce_visibility,
    load_visibility_config,
    parse_visibility_config,
)

SUFFIX = " (low confidence: dynamic access present in module)"

LIBRARY = {"visibility": {"mode": "library"}}

BARREL = {
    "pkg/lib.py": "class Widget:\n    pass\n",
    "pkg/__init__.py": "from pkg.lib import Widget\n",
}


# ── parsing (pypeeker.project) ──────────────────────────────────────────────


class TestParseVisibilityConfig:
    def test_defaults(self):
        cfg = parse_visibility_config(None)
        assert cfg == VisibilityConfig()
        assert cfg.mode == "app"
        assert not cfg.is_library
        assert cfg.public_roots == ()
        assert cfg.allow_decorators == ()

    def test_parses_all_fields(self):
        cfg = parse_visibility_config(
            {
                "mode": "library",
                "public-roots": ["pkg", "pkg.api"],
                "allow-decorators": ["register*"],
            }
        )
        assert cfg.is_library
        assert cfg.public_roots == ("pkg", "pkg.api")
        assert cfg.allow_decorators == ("register*",)

    def test_unknown_mode_falls_back_to_app(self):
        assert parse_visibility_config({"mode": "libary"}).mode == "app"
        assert parse_visibility_config({"mode": 7}).mode == "app"

    def test_non_mapping_input_yields_defaults(self):
        assert parse_visibility_config("library") == VisibilityConfig()

    def test_string_values_coerced_to_single_element_tuples(self):
        cfg = parse_visibility_config(
            {"public-roots": "pkg", "allow-decorators": "register"}
        )
        assert cfg.public_roots == ("pkg",)
        assert cfg.allow_decorators == ("register",)


class TestEffectivePublicRoots:
    def test_app_mode_protects_nothing(self):
        cfg = VisibilityConfig(mode="app", public_roots=("pkg",))
        assert cfg.effective_public_roots(["pkg"]) == ()

    def test_library_mode_defaults_to_top_level_packages(self):
        cfg = VisibilityConfig(mode="library")
        assert cfg.effective_public_roots(["pkg", "other", "pkg"]) == (
            "other",
            "pkg",
        )

    def test_explicit_roots_override_default(self):
        cfg = VisibilityConfig(mode="library", public_roots=("pkg.api",))
        assert cfg.effective_public_roots(["pkg", "other"]) == ("pkg.api",)


class TestLoadVisibilityConfig:
    def test_missing_pyproject_returns_defaults(self, tmp_path):
        assert load_visibility_config(tmp_path) == VisibilityConfig()

    def test_reads_visibility_table(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            "[tool.pypeeker.visibility]\n"
            'mode = "library"\n'
            'public-roots = ["pkg"]\n'
            'allow-decorators = ["register"]\n'
        )
        cfg = load_visibility_config(tmp_path)
        assert cfg == VisibilityConfig(
            mode="library",
            public_roots=("pkg",),
            allow_decorators=("register",),
        )

    def test_section_present_without_visibility_returns_defaults(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.pypeeker]\nrules = []\n")
        assert load_visibility_config(tmp_path) == VisibilityConfig()


class TestCoerceVisibility:
    def test_passes_through_instances(self):
        cfg = VisibilityConfig(mode="library")
        assert coerce_visibility(cfg) is cfg

    def test_parses_raw_mappings(self):
        assert coerce_visibility({"mode": "library"}).is_library

    def test_anything_else_yields_defaults(self):
        assert coerce_visibility(None) == VisibilityConfig()
        assert coerce_visibility(["library"]) == VisibilityConfig()


# ── injection (check.config) ────────────────────────────────────────────────


class TestCheckConfigVisibility:
    def test_visibility_field_populated_from_section(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            "[tool.pypeeker]\n"
            'rules = ["unused-public-symbol"]\n'
            "[tool.pypeeker.visibility]\n"
            'mode = "library"\n'
        )
        cfg = load_config(tmp_path)
        assert cfg.visibility == VisibilityConfig(mode="library")

    def test_raw_table_injected_into_every_enabled_rule(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            "[tool.pypeeker]\n"
            'rules = ["unused-public-symbol", "over-exposed-export"]\n'
            "[tool.pypeeker.visibility]\n"
            'mode = "library"\n'
            'public-roots = ["pkg"]\n'
        )
        cfg = load_config(tmp_path)
        expected = {"mode": "library", "public-roots": ["pkg"]}
        for rule in cfg.rules:
            assert cfg.rule_options[rule]["visibility"] == expected

    def test_injection_preserves_existing_rule_options(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            "[tool.pypeeker]\n"
            'rules = ["over-exposed-module-symbol"]\n'
            "[tool.pypeeker.visibility]\n"
            'mode = "library"\n'
            "[tool.pypeeker.over-exposed-module-symbol]\n"
            'allow-decorators = ["register"]\n'
        )
        options = load_config(tmp_path).rule_options["over-exposed-module-symbol"]
        assert options["allow-decorators"] == ["register"]
        assert options["visibility"] == {"mode": "library"}

    def test_visibility_is_not_a_rule_options_subsection(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            "[tool.pypeeker]\n"
            "rules = []\n"
            "[tool.pypeeker.visibility]\n"
            'mode = "library"\n'
        )
        assert "visibility" not in load_config(tmp_path).rule_options

    def test_no_section_means_no_injection_and_defaults(self, tmp_path):
        # Regression: projects without [tool.pypeeker.visibility] see exactly
        # the same CheckConfig shape as before the feature existed.
        cfg = load_config(tmp_path)
        assert cfg == CheckConfig()
        assert cfg.visibility == VisibilityConfig()
        (tmp_path / "pyproject.toml").write_text(
            "[tool.pypeeker]\n"
            'rules = ["require-docstrings"]\n'
            "[tool.pypeeker.require-docstrings]\n"
            'kinds = ["function"]\n'
        )
        cfg = load_config(tmp_path)
        assert cfg.visibility == VisibilityConfig()
        assert cfg.rule_options["require-docstrings"] == {"kinds": ["function"]}


# ── rule behaviour ──────────────────────────────────────────────────────────


@pytest.fixture
def run_rule(indexed_project):
    """Index ``files``, build a CheckContext, run ``rule`` -> messages set."""

    def _run(rule, files, options=None):
        _, store = indexed_project(files)
        indexes = [
            idx
            for idx in (store.load(p) for p in store.list_indexed_files())
            if idx is not None
        ]
        context = CheckContext(store, indexes)
        return {v.message for v in rule(context, options or {})}

    return _run


class TestLibraryModePublicRoots:
    def test_app_mode_flags_unconsumed_export(self, run_rule):
        # The published-API problem this feature solves: in app mode the
        # unconsumed barrel export is flagged.
        msgs = run_rule(over_exposed_export, dict(BARREL))
        assert any("'Widget'" in m for m in msgs)

    def test_library_mode_default_roots_protect_export(self, run_rule):
        msgs = run_rule(over_exposed_export, dict(BARREL), LIBRARY)
        assert not any("'Widget'" in m for m in msgs)

    def test_explicit_public_root_protects_export(self, run_rule):
        msgs = run_rule(
            over_exposed_export,
            dict(BARREL),
            {"visibility": {"mode": "library", "public-roots": ["pkg"]}},
        )
        assert not any("'Widget'" in m for m in msgs)

    def test_explicit_roots_override_default_protection(self, run_rule):
        # Explicit list replaces the top-level-packages default: a barrel
        # outside the listed roots is fair game again even in library mode.
        msgs = run_rule(
            over_exposed_export,
            dict(BARREL),
            {"visibility": {"mode": "library", "public-roots": ["other"]}},
        )
        assert any("'Widget'" in m for m in msgs)

    def test_nested_barrel_under_root_is_protected(self, run_rule):
        files = {
            "pkg/api/lib.py": "class Widget:\n    pass\n",
            "pkg/api/__init__.py": "from pkg.api.lib import Widget\n",
            "pkg/__init__.py": "",
        }
        flagged = run_rule(over_exposed_export, files)
        assert any("'Widget'" in m for m in flagged)
        msgs = run_rule(
            over_exposed_export,
            files,
            {"visibility": {"mode": "library", "public-roots": ["pkg.api"]}},
        )
        assert not any("'Widget'" in m for m in msgs)

    def test_public_roots_ignored_in_app_mode(self, run_rule):
        msgs = run_rule(
            over_exposed_export,
            dict(BARREL),
            {"visibility": {"mode": "app", "public-roots": ["pkg"]}},
        )
        assert any("'Widget'" in m for m in msgs)

    def test_barrel_exported_symbol_exempt_from_all_four_rules(self, run_rule):
        # In library mode the barrel-exported definition is protected across
        # the board. (For unused-public-symbol, over-exposed-module-symbol
        # and test-only-production-code the unconditional barrel exemption
        # already covers this in app mode too — these assertions pin the
        # explicit library contract on top of it.)
        files = dict(BARREL)
        files["tests/test_lib.py"] = "from pkg.lib import Widget\n\nWidget()\n"
        for rule in (
            unused_public_symbol,
            over_exposed_module_symbol,
            over_exposed_export,
            only_from_tests_rule,
        ):
            msgs = run_rule(rule, files, LIBRARY)
            assert not any("'Widget'" in m for m in msgs), rule

    def test_library_mode_does_not_protect_non_exported_symbols(self, run_rule):
        # Library mode is not a blanket waiver: a symbol no barrel exports is
        # still dead code.
        files = {"pkg/lib.py": "def orphan():\n    return 1\n"}
        assert any(
            "'orphan'" in m
            for m in run_rule(unused_public_symbol, files, LIBRARY)
        )
        assert any(
            "'orphan'" in m
            for m in run_rule(over_exposed_module_symbol, files, LIBRARY)
        )

    def test_rules_accept_parsed_visibility_config_instance(self, run_rule):
        # coerce_visibility lets tests/plugins pass the dataclass directly.
        msgs = run_rule(
            over_exposed_export,
            dict(BARREL),
            {"visibility": VisibilityConfig(mode="library")},
        )
        assert not any("'Widget'" in m for m in msgs)


REGISTRY = (
    "def register(f):\n    return f\n\n"
    "@register\ndef handler():\n    return 1\n"
)


class TestGlobalAllowDecorators:
    GLOBAL = {"visibility": {"allow-decorators": ["register"]}}

    def test_unused_public_symbol_gains_decorator_exemption(self, run_rule):
        files = {"pkg/lib.py": REGISTRY}
        flagged = run_rule(unused_public_symbol, files)
        assert any("'handler'" in m for m in flagged)
        # Via the rule's own (new) option…
        msgs = run_rule(
            unused_public_symbol, files, {"allow-decorators": ["register"]}
        )
        assert not any("'handler'" in m for m in msgs)
        # …and via the global visibility list.
        msgs = run_rule(unused_public_symbol, files, self.GLOBAL)
        assert not any("'handler'" in m for m in msgs)

    def test_over_exposed_module_symbol_merges_global_list(self, run_rule):
        files = {"pkg/lib.py": REGISTRY}
        flagged = run_rule(over_exposed_module_symbol, files)
        assert any("'handler'" in m for m in flagged)
        msgs = run_rule(over_exposed_module_symbol, files, self.GLOBAL)
        assert not any("'handler'" in m for m in msgs)
        # Per-rule and global lists merge rather than replace each other.
        msgs = run_rule(
            over_exposed_module_symbol,
            files,
            {"allow-decorators": ["other"], **self.GLOBAL},
        )
        assert not any("'handler'" in m for m in msgs)

    def test_test_only_production_code_gains_decorator_exemption(self, run_rule):
        files = {
            "pkg/lib.py": REGISTRY,
            "tests/test_lib.py": "from pkg.lib import handler\n\nhandler()\n",
        }
        flagged = run_rule(only_from_tests_rule, files)
        assert any("'handler'" in m for m in flagged)
        msgs = run_rule(only_from_tests_rule, files, self.GLOBAL)
        assert not any("'handler'" in m for m in msgs)
        msgs = run_rule(
            only_from_tests_rule, files, {"allow-decorators": ["register"]}
        )
        assert not any("'handler'" in m for m in msgs)


DYNAMIC_MODULE = (
    "def orphan():\n    return 1\n\nvalue = getattr(object, 'x', None)\n"
)


class TestDynamicAccessProximity:
    def test_unused_public_symbol_suffixes_not_suppresses(self, run_rule):
        msgs = run_rule(unused_public_symbol, {"pkg/lib.py": DYNAMIC_MODULE})
        flagged = [m for m in msgs if "'orphan'" in m]
        assert flagged, "dynamic access must downgrade, not suppress"
        assert all(m.endswith(SUFFIX) for m in flagged)

    def test_no_dynamic_access_means_no_suffix(self, run_rule):
        msgs = run_rule(
            unused_public_symbol,
            {"pkg/lib.py": "def orphan():\n    return 1\n"},
        )
        flagged = [m for m in msgs if "'orphan'" in m]
        assert flagged
        assert all(not m.endswith(SUFFIX) for m in flagged)
        assert all(
            m == "public function 'orphan' has no references in the project"
            for m in flagged
        )

    def test_dynamic_access_elsewhere_does_not_suffix(self, run_rule):
        # Only the *defining* module's dynamic access downgrades confidence.
        msgs = run_rule(
            unused_public_symbol,
            {
                "pkg/lib.py": "def orphan():\n    return 1\n",
                "pkg/other.py": "value = getattr(object, 'x', None)\n",
            },
        )
        flagged = [m for m in msgs if "'orphan'" in m]
        assert flagged
        assert all(not m.endswith(SUFFIX) for m in flagged)

    def test_over_exposed_module_symbol_suffixes(self, run_rule):
        msgs = run_rule(
            over_exposed_module_symbol, {"pkg/lib.py": DYNAMIC_MODULE}
        )
        flagged = [m for m in msgs if "'orphan'" in m]
        assert flagged
        assert all(m.endswith(SUFFIX) for m in flagged)

    def test_over_exposed_export_suffixes_on_dynamic_barrel(self, run_rule):
        # The export symbol is defined in the barrel module; getattr there
        # (e.g. a module __getattr__ implementation) downgrades confidence.
        files = {
            "pkg/lib.py": "class Widget:\n    pass\n",
            "pkg/__init__.py": (
                "from pkg.lib import Widget\n\n"
                "value = getattr(object, 'x', None)\n"
            ),
        }
        msgs = run_rule(over_exposed_export, files)
        flagged = [m for m in msgs if "'Widget'" in m]
        assert flagged
        assert all(m.endswith(SUFFIX) for m in flagged)

    def test_test_only_production_code_suffixes(self, run_rule):
        files = {
            "pkg/lib.py": (
                "def helper():\n    return 1\n\n"
                "value = getattr(object, 'x', None)\n"
            ),
            "tests/test_lib.py": "from pkg.lib import helper\n\nhelper()\n",
        }
        msgs = run_rule(only_from_tests_rule, files)
        flagged = [m for m in msgs if "'helper'" in m]
        assert flagged
        assert all(m.endswith(SUFFIX) for m in flagged)

    def test_globals_reference_also_counts(self, run_rule):
        msgs = run_rule(
            unused_public_symbol,
            {
                "pkg/lib.py": (
                    "def orphan():\n    return 1\n\nnames = globals()\n"
                )
            },
        )
        flagged = [m for m in msgs if "'orphan'" in m]
        assert flagged
        assert all(m.endswith(SUFFIX) for m in flagged)
