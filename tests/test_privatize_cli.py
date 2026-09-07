"""End-to-end tests for the privatize CLI command (TASK-97; regrammared TASK-126).

``pypeeker privatize`` runs the demotion-feeding rules through
:func:`pypeeker.app.run_privatize` — each nominated row's symbol id comes off
its DSL anchor, and the surviving repairs are routed through the batch
demotion planner (:func:`pypeeker.refactor.plan_privatize`) into ONE flattened
transaction. Since TASK-126 it plans AND applies that transaction immediately,
like every other mutating command; ``--plan`` writes it PENDING instead. Tests
drive the real CLI over a tmp fixture package — ``--plan`` leaves the tree
untouched with an inspectable pending transaction, the default applies renames
(including a defining-module ``__all__`` rewrite), skips are reported with
stable reasons, rules are selectable, and nothing-plannable exits 1.

Note on barrels: a barrel-exported symbol can never reach this command —
all three demotion-feeding rules exempt barrel re-exported definitions as
deliberate API surface — so the barrel-rewrite path is exercised at the
planner level (test_privatize.py), not here.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from pypeeker.cli import main

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
    # heuristic confidence: ghost's module uses getattr, so its rows arrive
    # HEURISTIC, fall below DEMOTE's floor, and are always excluded.
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


class TestPrivatizePlanOnly:
    def test_plan_reports_executed_and_skipped_without_touching_tree(
        self, tmp_path, monkeypatch
    ):
        runner = _project(tmp_path, monkeypatch, FIXTURE)
        before = {
            name: (tmp_path / "src" / name).read_text() for name in FIXTURE
        }

        code, output = _invoke(runner, ["privatize", "--plan"])
        assert code == 0, output
        assert output["tx_id"]
        assert "applied" not in output
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

        # A later manual apply lands the same edits the default path would
        # have.
        code, applied = _invoke(runner, ["apply", output["tx_id"]])
        assert code == 0, applied
        assert applied["status"] == "applied"
        assert (tmp_path / "src" / "pkg" / "dead.py").read_text().startswith(
            "def _orphan("
        )


class TestPrivatizeApply:
    def test_applies_by_default_lands_renames_including_dunder_all(
        self, tmp_path, monkeypatch
    ):
        runner = _project(tmp_path, monkeypatch, FIXTURE)
        code, output = _invoke(runner, ["privatize"])
        assert code == 0, output
        assert output["applied"] is True
        # The applier's result is merged in rather than collapsed to the
        # bool, so a stale index entry after a successful edit stays
        # reportable (pre-TASK-126 this data came from the separate 'apply').
        assert output["files_reindex_failed"] == []
        assert sorted(output["files_reindexed"]) == sorted(output["files_affected"])

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

        # The transaction is recorded APPLIED, and rollback restores every
        # touched file byte-for-byte.
        code, shown = _invoke(runner, ["transactions", "show", output["tx_id"]])
        assert code == 0, shown
        assert shown["header"]["status"] == "applied"
        code, rolled = _invoke(runner, ["rollback", output["tx_id"]])
        assert code == 0, rolled
        assert rolled["status"] == "rolled_back"
        assert (src / "pkg" / "mod.py").read_text() == FIXTURE["pkg/mod.py"]

    def test_rerun_after_apply_has_nothing_plannable(
        self, tmp_path, monkeypatch
    ):
        runner = _project(tmp_path, monkeypatch, FIXTURE)
        code, _ = _invoke(runner, ["privatize"])
        assert code == 0
        # Everything demotable is private now; only heuristic/collision
        # nominations remain and they all skip -> no transaction, exit 1.
        code, output = _invoke(runner, ["privatize"])
        assert code == 1
        assert output["code"] == "no-candidates"
        assert "error" in output


    def test_apply_failure_reports_clean_error_without_success_keys(
        self, tmp_path, monkeypatch
    ):
        # When the default apply-after-plan fails, the command reports a
        # clean error and exits 1 — it must NOT graft an "error" key onto the
        # otherwise-success report dict (which would look like both a success
        # and a failure), and the report's "applied"/"executed" success keys
        # must not appear alongside the error.
        from pypeeker.refactor import ApplyError

        class _FailingApplier:
            def __init__(self, *args, **kwargs):
                pass

            def apply(self, tx_id):
                raise ApplyError("integrity check failed")

        # privatize's apply step goes through the same shared
        # _finish_mutation tail every mutating command uses (cli.py), which
        # resolves TransactionApplier from pypeeker.refactor at call time.
        monkeypatch.setattr(
            "pypeeker.refactor.TransactionApplier", _FailingApplier
        )
        runner = _project(tmp_path, monkeypatch, FIXTURE)
        code, output = _invoke(runner, ["privatize"])
        assert code == 1
        assert output["error"] == "integrity check failed"
        assert output["code"] == "apply-failed"
        assert "tx_id" in output
        assert "applied" not in output
        assert "executed" not in output

        # The stub raises before touching anything — the pre-flight shape,
        # which leaves the transaction PENDING. A real mid-apply failure
        # marks it FAILED instead; both statuses come from the applier, not
        # from this command (pinned in test_rename_cli.py, which covers the
        # shared _finish_mutation tail for every mutating command).
        from pypeeker.storage import TransactionStore

        header = TransactionStore(tmp_path).load(output["tx_id"]).header
        assert header.status.value == "pending"


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
        assert output["code"] == "no-candidates"
        assert _skip_reasons(output) == {
            ("pkg.dyn:ghost", "heuristic-confidence")
        }

    def test_include_heuristic_is_no_longer_an_option(
        self, tmp_path, monkeypatch
    ):
        # The floor is an attribute of the one shared DEMOTE mutation, so the
        # skip above is no longer waivable and the flag that waived it is gone.
        runner = _project(
            tmp_path, monkeypatch, {"pkg/dyn.py": FIXTURE["pkg/dyn.py"]}
        )
        result = runner.invoke(
            main,
            [
                "privatize",
                "--rule", "unused-public-symbol",
                "--include-heuristic",
            ],
        )
        assert result.exit_code == 2
        assert "--include-heuristic" in result.output
        assert "def ghost():" in (
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
            "error": "no demotable candidates: nothing was plannable",
            "code": "no-candidates",
            "skipped": [],
            "dropped": [],
        }

