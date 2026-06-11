"""End-to-end tests for the privatize CLI command (TASK-97).

``pypeeker privatize`` runs the demotion-feeding check rules, extracts the
nominated symbols from the findings via
:func:`pypeeker.check.demotion.demote_entry`, and routes them through the
batch demotion planner (:func:`pypeeker.refactor.privatize.plan_privatize`)
into ONE flattened transaction. Tests drive the real CLI over a tmp fixture
package — plan-only leaves the tree untouched with an inspectable pending
transaction, ``--apply`` lands renames (including a defining-module
``__all__`` rewrite), skips are reported with stable reasons, rules are
selectable, and nothing-plannable exits 1 — plus per-rule unit tests of the
``demote_entry`` finding-to-pair extraction.

Note on barrels: a barrel-exported symbol can never reach this command —
all three demotion-feeding rules exempt barrel re-exported definitions as
deliberate API surface — so the barrel-rewrite path is exercised at the
planner level (test_privatize.py), not here.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

# Aliased on import: the rule's name starts with ``test_`` and pytest would
# otherwise try to collect it as a test function.
from pypeeker.check.builtin.test_only_production_code import (
    TEST_ONLY_PRODUCTION_CODE,
    _test_only_production_code as only_from_tests_rule,
)
from pypeeker.check.builtin.visibility import (
    OVER_EXPOSED_MODULE_SYMBOL,
    _over_exposed_module_symbol as over_exposed_module_symbol,
)
from pypeeker.check.context import CheckContext
from pypeeker.check.demotion import DEMOTION_RULES, demote_entry
from pypeeker.check.models import Violation
from pypeeker.check.rules import UNUSED_PUBLIC_SYMBOL, unused_public_symbol
from pypeeker.cli import _PRIVATIZE_RULES, main

PYPROJECT = (
    '[project]\nname = "test"\n'
    "[tool.pypeeker]\n"
    'src = ["src"]\n'
    "rules = []\n"
    "[tool.pypeeker.over-exposed-module-symbol]\n"
    'allow = ["pkg.mod:keep_me"]\n'
)

FIXTURE = {
    # over-exposed-module-symbol: local_helper is public but module-local;
    # keep_me would be too, but the pyproject option above exempts it.
    # used_everywhere has a cross-module consumer (app.py) and stays public.
    # The defining-module __all__ entry must follow an executed demotion.
    "pkg/__init__.py": "",
    "pkg/mod.py": (
        '__all__ = ["local_helper", "used_everywhere"]\n'
        "\n"
        "\n"
        "def local_helper():\n"
        "    return 1\n"
        "\n"
        "\n"
        "def keep_me():\n"
        "    return 2\n"
        "\n"
        "\n"
        "def used_everywhere():\n"
        "    return local_helper() + keep_me()\n"
    ),
    "app.py": "from pkg.mod import used_everywhere\n\nused_everywhere()\n",
    # unused-public-symbol (and over-exposed: zero refs): orphan.
    "pkg/dead.py": "def orphan():\n    return 1\n",
    # test-only-production-code: fixture_helper's only consumer is a test
    # module (matched by the default '**/test_*.py' glob).
    "pkg/prod.py": "def fixture_helper():\n    return 1\n",
    "pkg/test_mod.py": (
        "from pkg.prod import fixture_helper\n\nfixture_helper()\n"
    ),
    # heuristic confidence: ghost's module uses getattr, so its findings are
    # HEURISTIC and excluded from the batch unless --include-heuristic.
    "pkg/dyn.py": (
        "def ghost():\n"
        "    return 1\n"
        "\n"
        "\n"
        'value = getattr(object, "x", None)\n'
    ),
    # name collision: twin would demote to _twin, which already exists.
    "pkg/coll.py": (
        "def twin():\n"
        "    return _twin()\n"
        "\n"
        "\n"
        "def _twin():\n"
        "    return 1\n"
    ),
}


def _project(tmp_path: Path, monkeypatch, files: dict[str, str]) -> CliRunner:
    """A cwd'd tmp project with ``files`` under src/, indexed via the CLI."""
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    for name, content in files.items():
        path = tmp_path / "src" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main, ["index", str(tmp_path / "src")], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    return runner


def _invoke(runner: CliRunner, args: list[str]) -> tuple[int, dict | list]:
    """Invoke the CLI and parse its JSON output."""
    result = runner.invoke(main, args, catch_exceptions=False)
    return result.exit_code, json.loads(result.output)


def _executed_ids(output: dict) -> set[str]:
    return {entry["symbol_id"] for entry in output["executed"]}


def _skip_reasons(output: dict) -> set[tuple[str, str]]:
    return {(s["symbol_id"], s["reason"]) for s in output["skipped"]}


def test_cli_rule_choices_match_demotion_rules():
    # The CLI keeps the choice tuple as literals (lazy check import); this
    # pins it to the canonical list in check.demotion.
    assert _PRIVATIZE_RULES == DEMOTION_RULES


class TestPrivatizePlanOnly:
    def test_plan_reports_executed_and_skipped_without_touching_tree(
        self, tmp_path, monkeypatch
    ):
        runner = _project(tmp_path, monkeypatch, FIXTURE)
        before = {
            name: (tmp_path / "src" / name).read_text() for name in FIXTURE
        }

        code, output = _invoke(runner, ["privatize"])
        assert code == 0, output
        assert output["tx_id"]
        assert _executed_ids(output) == {
            "pkg.mod:local_helper",
            "pkg.dead:orphan",
            "pkg.prod:fixture_helper",
        }
        assert output["dropped"] == []
        reasons = _skip_reasons(output)
        # ghost is nominated by two rules, both findings heuristic.
        assert ("pkg.dyn:ghost", "heuristic-confidence") in reasons
        # twin collides with the existing _twin (both nominations skip).
        assert ("pkg.coll:twin", "name-collision") in reasons
        # orphan is nominated by both over-exposed and unused: the second
        # nomination skips as a pending collision with the first.
        assert ("pkg.dead:orphan", "pending-collision") in reasons
        # keep_me is exempted by the pyproject rule option: never nominated.
        assert all("keep_me" not in s["symbol_id"] for s in output["skipped"])
        assert output["edit_count"] > 0
        assert "src/pkg/mod.py" in output["files_affected"]

        # Plan-only: the real tree is untouched...
        after = {
            name: (tmp_path / "src" / name).read_text() for name in FIXTURE
        }
        assert after == before

        # ...and the persisted transaction is inspectable and PENDING.
        code, shown = _invoke(runner, ["transactions", "show", output["tx_id"]])
        assert code == 0, shown
        assert shown["header"]["operation"] == "privatize"
        assert shown["header"]["status"] == "pending"
        assert len(shown["edits"]) == output["edit_count"]


class TestPrivatizeApply:
    def test_apply_lands_renames_including_dunder_all(
        self, tmp_path, monkeypatch
    ):
        runner = _project(tmp_path, monkeypatch, FIXTURE)
        code, output = _invoke(runner, ["privatize", "--apply"])
        assert code == 0, output
        assert output["applied"]["status"] == "applied"

        src = tmp_path / "src"
        mod = (src / "pkg" / "mod.py").read_text()
        assert "def _local_helper():" in mod
        assert "return _local_helper() + keep_me()" in mod
        # The defining module's stale __all__ entry follows the rename.
        assert '__all__ = ["_local_helper", "used_everywhere"]' in mod
        assert (src / "pkg" / "dead.py").read_text().startswith("def _orphan(")
        assert (src / "pkg" / "prod.py").read_text().startswith(
            "def _fixture_helper("
        )
        # The test-module consumer was rewritten with the rename.
        assert (src / "pkg" / "test_mod.py").read_text() == (
            "from pkg.prod import _fixture_helper\n\n_fixture_helper()\n"
        )
        # Untouched bystanders.
        assert "def keep_me():" in mod
        assert (src / "app.py").read_text() == FIXTURE["app.py"]

    def test_rerun_after_apply_has_nothing_plannable(
        self, tmp_path, monkeypatch
    ):
        runner = _project(tmp_path, monkeypatch, FIXTURE)
        code, _ = _invoke(runner, ["privatize", "--apply"])
        assert code == 0
        # Everything demotable is private now; only heuristic/collision
        # nominations remain and they all skip -> no transaction, exit 1.
        code, output = _invoke(runner, ["privatize"])
        assert code == 1
        assert output["tx_id"] is None
        assert output["executed"] == []


class TestRuleSelection:
    def test_single_rule_restricts_nominations(self, tmp_path, monkeypatch):
        runner = _project(tmp_path, monkeypatch, FIXTURE)
        code, output = _invoke(
            runner, ["privatize", "--rule", "test-only-production-code"]
        )
        assert code == 0, output
        assert _executed_ids(output) == {"pkg.prod:fixture_helper"}

    def test_repeated_rules_combine(self, tmp_path, monkeypatch):
        runner = _project(tmp_path, monkeypatch, FIXTURE)
        code, output = _invoke(
            runner,
            [
                "privatize",
                "--rule", "unused-public-symbol",
                "--rule", "test-only-production-code",
            ],
        )
        assert code == 0, output
        assert _executed_ids(output) == {
            "pkg.dead:orphan",
            "pkg.prod:fixture_helper",
        }
        # local_helper has in-module references: only over-exposed (not
        # selected here) nominates it.
        assert all(
            "local_helper" not in s["symbol_id"] for s in output["skipped"]
        )

    def test_unknown_rule_is_rejected_by_click(self, tmp_path, monkeypatch):
        runner = _project(tmp_path, monkeypatch, {"m.py": "x = 1\n"})
        result = runner.invoke(main, ["privatize", "--rule", "no-such-rule"])
        assert result.exit_code == 2
        assert "no-such-rule" in result.output


class TestHeuristicGate:
    def test_heuristic_findings_skip_by_default(self, tmp_path, monkeypatch):
        runner = _project(
            tmp_path, monkeypatch, {"pkg/dyn.py": FIXTURE["pkg/dyn.py"]}
        )
        code, output = _invoke(
            runner, ["privatize", "--rule", "unused-public-symbol"]
        )
        assert code == 1  # nothing plannable: the only nomination skipped
        assert output["tx_id"] is None
        assert _skip_reasons(output) == {
            ("pkg.dyn:ghost", "heuristic-confidence")
        }

    def test_include_heuristic_demotes_dynamic_module_symbols(
        self, tmp_path, monkeypatch
    ):
        runner = _project(
            tmp_path, monkeypatch, {"pkg/dyn.py": FIXTURE["pkg/dyn.py"]}
        )
        code, output = _invoke(
            runner,
            [
                "privatize",
                "--rule", "unused-public-symbol",
                "--include-heuristic",
                "--apply",
            ],
        )
        assert code == 0, output
        assert _executed_ids(output) == {"pkg.dyn:ghost"}
        assert "def _ghost():" in (
            tmp_path / "src" / "pkg" / "dyn.py"
        ).read_text()


class TestNothingPlannable:
    def test_clean_project_exits_one_with_empty_report(
        self, tmp_path, monkeypatch
    ):
        files = {
            "lib.py": "def helper():\n    return 1\n",
            "app.py": "from lib import helper\n\nhelper()\n",
        }
        runner = _project(tmp_path, monkeypatch, files)
        code, output = _invoke(runner, ["privatize"])
        assert code == 1
        assert output == {
            "tx_id": None,
            "executed": [],
            "dropped": [],
            "skipped": [],
            "warnings": [],
            "files_affected": [],
            "edit_count": 0,
        }


# ---------------------------------------------------------------------------
# demote_entry: per-rule extraction of (symbol_id, confidence) pairs
# ---------------------------------------------------------------------------


class TestDemoteEntry:
    def _run_rule(self, indexed_project, rule, files, options=None):
        _, store = indexed_project(files)
        indexes = [
            idx
            for idx in (store.load(p) for p in store.list_indexed_files())
            if idx is not None
        ]
        return rule(CheckContext(store, indexes), options or {})

    def test_over_exposed_module_symbol_finding(self, indexed_project):
        violations = self._run_rule(
            indexed_project,
            over_exposed_module_symbol,
            {
                "pkg/lib.py": "def helper():\n    return 1\n\nhelper()\n",
                "pkg/app.py": "x = 1\n",
            },
        )
        flagged = [v for v in violations if v.rule == OVER_EXPOSED_MODULE_SYMBOL]
        assert [demote_entry(v) for v in flagged] == [
            ("pkg.lib:helper", "declared")
        ]

    def test_unused_public_symbol_finding(self, indexed_project):
        violations = self._run_rule(
            indexed_project,
            unused_public_symbol,
            {"pkg/lib.py": "def orphan():\n    return 1\n"},
        )
        assert [demote_entry(v) for v in violations] == [
            ("pkg.lib:orphan", "declared")
        ]

    def test_heuristic_confidence_travels_with_the_pair(self, indexed_project):
        violations = self._run_rule(
            indexed_project,
            unused_public_symbol,
            {"pkg/lib.py": FIXTURE["pkg/dyn.py"]},
        )
        assert [demote_entry(v) for v in violations] == [
            ("pkg.lib:ghost", "heuristic")
        ]

    def test_test_only_production_code_finding(self, indexed_project):
        violations = self._run_rule(
            indexed_project,
            only_from_tests_rule,
            {
                "pkg/lib.py": "def helper():\n    return 1\n",
                "tests/test_lib.py": (
                    "from pkg.lib import helper\n\nhelper()\n"
                ),
            },
        )
        flagged = [v for v in violations if v.rule == TEST_ONLY_PRODUCTION_CODE]
        assert [demote_entry(v) for v in flagged] == [
            ("pkg.lib:helper", "declared")
        ]

    def test_other_rules_return_none(self):
        violation = Violation(
            file_path="m.py",
            line=1,
            rule="naming-conventions",
            message=(
                "function 'm:badName' does not match the snake_case naming "
                "convention — suggested name: 'bad_name'"
            ),
        )
        assert demote_entry(violation) is None

    def test_format_drift_returns_none(self):
        # A violation under a demotion rule whose message doesn't match the
        # owning rule's format (e.g. a custom rule reusing the name).
        violation = Violation(
            file_path="m.py",
            line=1,
            rule=UNUSED_PUBLIC_SYMBOL,
            message="something else entirely",
        )
        assert demote_entry(violation) is None
