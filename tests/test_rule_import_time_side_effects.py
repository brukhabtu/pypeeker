"""Tests for the import-time-side-effects builtin rule ("imports must be free")."""

from __future__ import annotations

from pypeeker.dsl import RULES
from pypeeker.dsl.impurity import DEFAULT_ALLOW

RULE = "import-time-side-effects"

IMPURE_HELPER = "def do_io(path):\n    open(path)\n"
PURE_HELPER = "def add(a, b):\n    return a + b\n"


class TestImportTimeSideEffects:
    # -- shape 1: bare/builtin calls --------------------------------------

    def test_module_scope_open_flagged(self, run_dsl_rule):
        violations = run_dsl_rule(
            RULE,
            {"pkg/mod.py": 'data = open("f.txt")\n'}
        )
        assert len(violations) == 1
        v = violations[0]
        assert v.rule == RULE
        assert "'open'" in v.message
        assert "impure-builtin policy" in v.message
        assert v.line == 1  # 1-indexed

    def test_function_scope_impure_call_not_flagged(self, run_dsl_rule):
        violations = run_dsl_rule(
            RULE,
            {"pkg/mod.py": 'def f():\n    open("f.txt")\n    print("x")\n'},
        )
        assert violations == []

    def test_class_body_impure_call_flagged(self, run_dsl_rule):
        violations = run_dsl_rule(
            RULE,
            {"pkg/mod.py": 'class Config:\n    data = open("f.txt")\n'},
        )
        assert len(violations) == 1
        assert "'open'" in violations[0].message
        assert violations[0].line == 2

    def test_method_body_inside_class_not_flagged(self, run_dsl_rule):
        # Only the class *body* runs at import; method bodies run when called.
        violations = run_dsl_rule(
            RULE,
            {"pkg/mod.py": 'class C:\n    def run(self):\n        open("f")\n'},
        )
        assert violations == []

    # -- shape 2: module-qualified calls -----------------------------------

    def test_module_scope_subprocess_run_flagged(self, run_dsl_rule):
        violations = run_dsl_rule(
            RULE,
            {"pkg/mod.py": 'import subprocess\n\nsubprocess.run(["ls"])\n'},
        )
        assert len(violations) == 1
        v = violations[0]
        assert "'subprocess.run'" in v.message
        assert "impure-call policy" in v.message
        assert v.line == 3

    def test_aliased_module_import_flagged(self, run_dsl_rule):
        violations = run_dsl_rule(
            RULE,
            {"pkg/mod.py": "import time as t\n\nNOW = t.time()\n"},
        )
        assert len(violations) == 1
        assert "'time.time'" in violations[0].message

    def test_pure_qualified_call_not_flagged(self, run_dsl_rule):
        violations = run_dsl_rule(
            RULE,
            {"pkg/mod.py": 'import os\n\nBASE = os.path.join("a", "b")\n'},
        )
        assert violations == []

    # -- shape 3: project-internal impure functions ------------------------

    def test_module_scope_call_to_impure_project_function_flagged(self, run_dsl_rule):
        violations = run_dsl_rule(
            RULE,
            {"pkg/mod.py": IMPURE_HELPER + '\ndo_io("f.txt")\n'},
        )
        assert len(violations) == 1
        v = violations[0]
        assert "'pkg.mod:do_io'" in v.message
        assert "impure project function" in v.message
        assert v.line == 4

    def test_module_scope_call_to_pure_project_function_not_flagged(self, run_dsl_rule):
        violations = run_dsl_rule(
            RULE,
            {"pkg/mod.py": PURE_HELPER + "\nTOTAL = add(1, 2)\n"},
        )
        assert violations == []

    def test_cross_file_impure_call_flagged(self, run_dsl_rule):
        violations = run_dsl_rule(
            RULE,
            {
                "pkg/helpers.py": IMPURE_HELPER,
                "pkg/app.py": 'from pkg.helpers import do_io\n\ndo_io("f")\n',
            },
        )
        assert len(violations) == 1
        assert "'pkg.helpers:do_io'" in violations[0].message
        assert violations[0].path == "pkg/app.py"

    # -- allow option / default allowlist ----------------------------------

    def test_default_allowlist_passes_logging_getlogger(self, run_dsl_rule):
        # The conventional module-level logger idiom is never flagged, even
        # when extra-impure would otherwise match it.
        files = {
            "pkg/mod.py": "import logging\n\nlogger = logging.getLogger(__name__)\n"
        }
        assert run_dsl_rule(RULE, files) == []
        assert run_dsl_rule(
            RULE,
            files, {"extra-impure": ["logging.getLogger"]}
        ) == []
        assert "logging.getLogger" in DEFAULT_ALLOW

    def test_allow_pattern_suppresses_call_by_name(self, run_dsl_rule):
        files = {"pkg/mod.py": 'data = open("f.txt")\n'}
        assert len(run_dsl_rule(RULE, files)) == 1
        assert run_dsl_rule(RULE, files, {"allow": ["open"]}) == []

    def test_allow_pattern_suppresses_whole_module(self, run_dsl_rule):
        files = {
            "pkg/settings.py": 'data = open("f.txt")\n',
            "pkg/mod.py": 'other = open("g.txt")\n',
        }
        violations = run_dsl_rule(
            RULE,
            files, {"allow": ["pkg.settings"]}
        )
        assert [v.path for v in violations] == ["pkg/mod.py"]

    def test_allow_pattern_matches_project_symbol_id(self, run_dsl_rule):
        files = {"pkg/mod.py": IMPURE_HELPER + '\ndo_io("f.txt")\n'}
        assert len(run_dsl_rule(RULE, files)) == 1
        assert run_dsl_rule(
            RULE,
            files, {"allow": ["pkg.mod:do_io"]}
        ) == []

    # -- extra-impure option ------------------------------------------------

    def test_extra_impure_dotted_extends_module_denylist(self, run_dsl_rule):
        files = {"pkg/mod.py": "import mypkg\n\nmypkg.db.commit()\n"}
        assert run_dsl_rule(RULE, files) == []
        violations = run_dsl_rule(
            RULE,
            files, {"extra-impure": ["mypkg.db.commit"]}
        )
        assert len(violations) == 1
        assert "'mypkg.db.commit'" in violations[0].message

    def test_extra_impure_bare_extends_builtin_denylist(self, run_dsl_rule):
        # 'log' is an unresolved bare name (e.g. star import): pure by default.
        files = {"pkg/mod.py": 'log("hello")\n'}
        assert run_dsl_rule(RULE, files) == []
        violations = run_dsl_rule(
            RULE,
            files, {"extra-impure": ["log"]}
        )
        assert len(violations) == 1
        assert "'log'" in violations[0].message

    def test_extra_impure_also_applies_to_project_function_bodies(self, run_dsl_rule):
        # The policy extension flows into the purity analysis of shape 3.
        src = "def setup():\n    log('hi')\n\nsetup()\n"
        files = {"pkg/mod.py": src}
        assert run_dsl_rule(RULE, files) == []
        violations = run_dsl_rule(RULE, files, {"extra-impure": ["log"]})
        assert len(violations) == 1
        assert "'pkg.mod:setup'" in violations[0].message

    # -- misc ----------------------------------------------------------------

    def test_one_violation_per_offending_call(self, run_dsl_rule):
        violations = run_dsl_rule(
            RULE,
            {"pkg/mod.py": 'a = open("a")\nb = open("b")\n'},
        )
        assert [v.line for v in violations] == [1, 2]

    def test_resolvable_by_id_in_the_rule_table(self):
        assert RULE in RULES

    def test_enabled_in_own_pyproject(self):
        # Enabled on pypeeker itself as a gate (TASK-112). Resolved from this
        # file (not cwd) because other test modules chdir without restoring.
        import tomllib
        from pathlib import Path

        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text())
        assert RULE in data["tool"]["pypeeker"]["rules"]
