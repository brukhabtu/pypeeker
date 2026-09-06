"""Direct tests for :mod:`pypeeker.app.privatize_run` (TASK-157, A12).

``run_dsl_privatize`` is the new engine's ``privatize`` workflow, built beside
the frozen :func:`pypeeker.app.privatize.run_privatize` and not yet wired to
the CLI. Nothing else in this segment can compare the two engines' privatize
reports on the same tree, so most tests here run **both** services over one
fixture and assert on the difference — which is exactly one thing, the derived
intent id.

.. note:: **TEMPORARY comparisons.** Every ``frozen``-named assertion in this
   file exists only while both services do. Phase B deletes
   ``app/privatize.py``; the comparisons go with it, and the tests keep their
   scenarios against the surviving service alone.
"""

from __future__ import annotations

import pytest

from pypeeker.app.privatize import run_privatize
from pypeeker.app.privatize_run import PrivatizeReport, run_dsl_privatize
from pypeeker.dsl.visibility import over_exposed_module_symbol
from pypeeker.project import VisibilityConfig
from pypeeker.storage import TransactionStore

ORPHAN = "def orphan():\n    return 1\n"


def _project(indexed_project, files: dict[str, str]):
    """An indexed_project rooted so the demotion rules see ``src/*.py``."""
    return indexed_project({f"src/{name}": content for name, content in files.items()})


def _report(outcome) -> dict:
    """The privatize CLI's JSON shape, minus the tx id and the derived ids."""
    return {
        "executed": [(e.symbol_id, e.new_name) for e in outcome.executed],
        "skipped": [(s.symbol_id, s.reason, s.detail) for s in outcome.skipped],
        "dropped": list(outcome.dropped),
        "warnings": outcome.warnings,
        "files_affected": (
            list(outcome.summary.files_affected) if outcome.summary else []
        ),
        "edit_count": outcome.summary.edit_count if outcome.summary else 0,
    }


class TestParityWithTheFrozenService:
    def test_duplicate_nomination_reports_pending_collision_on_both(
        self, indexed_project
    ):
        # One unreferenced public function is nominated by BOTH
        # over-exposed-module-symbol and unused-public-symbol, so two intents
        # arrive for one symbol. The frozen service reports the second as
        # pending-collision; so must this one, or the report changes shape on
        # essentially every real project.
        project_dir, store = _project(indexed_project, {"dead.py": ORPHAN})
        transaction_store = TransactionStore(project_dir)

        frozen = run_privatize(store, transaction_store, project_dir, ())
        fresh = run_dsl_privatize(store, transaction_store, project_dir)

        assert _report(fresh.outcome) == _report(frozen.outcome)
        assert [s.reason for s in fresh.outcome.skipped] == ["pending-collision"]
        # The one difference is fork #5's derived id, already in the ledger:
        # the origin rule now prefixes it.
        assert [e.intent_id for e in frozen.outcome.executed] == [
            "demote:src.dead:orphan"
        ]
        assert [e.intent_id for e in fresh.outcome.executed] == [
            "over-exposed-module-symbol:demote:src.dead:orphan"
        ]

    def test_skips_interleave_in_submitted_order_like_the_frozen_report(
        self, indexed_project
    ):
        # A pre-filter skip (name-collision, src/a.py) must be reported BEFORE
        # a mutation refusal (heuristic-confidence, src/z.py), because the
        # frozen pre-filter emits skips interleaved in submitted order and
        # cli.py does not sort them. Prepending all mutation refusals would
        # invert these two.
        project_dir, store = _project(
            indexed_project,
            {
                "a.py": "def helper():\n    pass\n\n\ndef _helper():\n    pass\n",
                "z.py": "def spooky():\n    return getattr(object(), 'x')\n",
            },
        )
        transaction_store = TransactionStore(project_dir)

        frozen = run_privatize(store, transaction_store, project_dir, ())
        fresh = run_dsl_privatize(store, transaction_store, project_dir)

        # Two nominations per symbol (over-exposed + unused-public), so four
        # rows: both pre-filter skips first, both mutation refusals after.
        assert [(s.symbol_id, s.reason) for s in fresh.outcome.skipped] == [
            ("src.a:helper", "name-collision"),
            ("src.a:helper", "name-collision"),
            ("src.z:spooky", "heuristic-confidence"),
            ("src.z:spooky", "heuristic-confidence"),
        ]
        assert _report(fresh.outcome) == _report(frozen.outcome)

    def test_a_barrel_exported_symbol_is_not_nominated_by_either_engine(
        self, indexed_project
    ):
        # Both engines exclude barrel exports from *nomination* — the DSL via
        # `not_(in_set(row.symbol_id, BARREL_EXPORTS))` in the shared candidate
        # prefix, the frozen rules via the same test — so the barrel-rewrite
        # path is unreachable from this service. The planner-level coverage of
        # that rewrite lives in tests/test_refactor_privatize_intents.py, which
        # submits the intent directly.
        project_dir, store = _project(
            indexed_project,
            {
                "pkg/__init__.py": (
                    '__all__ = ["helper"]\n\nfrom src.pkg.mod import helper\n'
                ),
                "pkg/mod.py": "def helper():\n    return 1\n",
            },
        )
        transaction_store = TransactionStore(project_dir)

        frozen = run_privatize(store, transaction_store, project_dir, ())
        fresh = run_dsl_privatize(store, transaction_store, project_dir)

        assert _report(fresh.outcome) == _report(frozen.outcome)
        assert [e.symbol_id for e in fresh.outcome.executed] == []
        assert fresh.outcome.warnings == []
        assert (project_dir / "src" / "pkg" / "__init__.py").read_text() == (
            '__all__ = ["helper"]\n\nfrom src.pkg.mod import helper\n'
        )

    def test_nothing_plannable_yields_no_summary(self, indexed_project):
        project_dir, store = _project(
            indexed_project,
            {
                "app.py": "from src.dead import used\n\nused()\n",
                "dead.py": "def used():\n    return 1\n",
            },
        )
        transaction_store = TransactionStore(project_dir)

        frozen = run_privatize(store, transaction_store, project_dir, ())
        fresh = run_dsl_privatize(store, transaction_store, project_dir)

        assert fresh.outcome.summary is None
        assert fresh.outcome.executed == []
        assert _report(fresh.outcome) == _report(frozen.outcome)

    def test_main_and_private_symbols_are_never_demoted(self, indexed_project):
        project_dir, store = _project(
            indexed_project,
            {"cli.py": "def main():\n    pass\n\n\ndef _helper():\n    pass\n"},
        )
        transaction_store = TransactionStore(project_dir)

        frozen = run_privatize(store, transaction_store, project_dir, ())
        fresh = run_dsl_privatize(store, transaction_store, project_dir)

        assert fresh.outcome.executed == []
        assert fresh.outcome.summary is None
        # Neither name is even nominated: _candidate_clauses filters main and
        # non-public symbols out of the *selection*, so no skip row appears
        # either — which is what the frozen engine does too.
        assert fresh.outcome.skipped == []
        assert _report(fresh.outcome) == _report(frozen.outcome)


class TestRawVisibilityTable:
    def test_a_project_declaring_visibility_options_does_not_raise(
        self, indexed_project
    ):
        project_dir, store = _project(indexed_project, {"dead.py": ORPHAN})
        (project_dir / "pyproject.toml").write_text(
            '[project]\nname = "t"\n\n'
            "[tool.pypeeker]\nsrc = [\"src\"]\n\n"
            '[tool.pypeeker.visibility]\nallow-decorators = ["*@keep*"]\n'
        )
        transaction_store = TransactionStore(project_dir)

        report = run_dsl_privatize(store, transaction_store, project_dir)

        assert [e.symbol_id for e in report.outcome.executed] == ["src.dead:orphan"]

    def test_a_parsed_visibility_config_is_refused_not_silently_ignored(self):
        # Regression pin for the straight-copy bug: app/privatize.py injects the
        # PARSED VisibilityConfig, and handing that to a DSL rule builder raises
        # TypeError. read_visibility_table exists so this path is never taken.
        with pytest.raises(TypeError, match="raw .tool.pypeeker.visibility."):
            over_exposed_module_symbol({"visibility": VisibilityConfig()})


class TestPerRuleOptions:
    def test_an_allow_pattern_applies_to_only_the_rule_that_declares_it(
        self, indexed_project
    ):
        # privatize_selections takes ONE shared option table for all three
        # rules; run_dsl_privatize calls it once per rule with that rule's own
        # table, so `allow` under [tool.pypeeker.over-exposed-module-symbol]
        # must not silence unused-public-symbol as well.
        project_dir, store = _project(indexed_project, {"dead.py": ORPHAN})
        (project_dir / "pyproject.toml").write_text(
            '[project]\nname = "t"\n\n'
            "[tool.pypeeker]\nsrc = [\"src\"]\n\n"
            "[tool.pypeeker.over-exposed-module-symbol]\n"
            'allow = ["src.dead:orphan"]\n'
        )
        transaction_store = TransactionStore(project_dir)

        report = run_dsl_privatize(store, transaction_store, project_dir)

        # Still demoted, but now nominated only by unused-public-symbol, so
        # there is no duplicate and no pending-collision row.
        assert [e.intent_id for e in report.outcome.executed] == [
            "unused-public-symbol:demote:src.dead:orphan"
        ]
        assert report.outcome.skipped == []

    def test_selecting_a_single_rule_restricts_the_run(self, indexed_project):
        project_dir, store = _project(indexed_project, {"dead.py": ORPHAN})
        transaction_store = TransactionStore(project_dir)

        report = run_dsl_privatize(
            store, transaction_store, project_dir, ("unused-public-symbol",)
        )

        assert [e.intent_id for e in report.outcome.executed] == [
            "unused-public-symbol:demote:src.dead:orphan"
        ]
        assert report.outcome.skipped == []

    def test_an_unknown_rule_name_is_refused(self, indexed_project):
        project_dir, store = _project(indexed_project, {"dead.py": ORPHAN})
        transaction_store = TransactionStore(project_dir)

        with pytest.raises(ValueError, match="is not a demotion rule"):
            run_dsl_privatize(
                store, transaction_store, project_dir, ("unused-imports",)
            )


class TestReportShape:
    def test_the_report_is_public_carries_only_the_outcome_and_is_barrel_exported(
        self, indexed_project
    ):
        import dataclasses

        from pypeeker import app

        assert app.PrivatizeReport is PrivatizeReport
        assert app.run_dsl_privatize is run_dsl_privatize
        assert [f.name for f in dataclasses.fields(PrivatizeReport)] == ["outcome"]
        assert "PrivatizeReport" in app.__all__
        assert "run_dsl_privatize" in app.__all__

    def test_run_dsl_privatize_takes_no_apply_plan_and_never_writes_the_tree(
        self, indexed_project
    ):
        import inspect

        signature = inspect.signature(run_dsl_privatize)
        assert list(signature.parameters) == [
            "store",
            "transaction_store",
            "root",
            "rules",
        ]

        project_dir, store = _project(indexed_project, {"dead.py": ORPHAN})
        transaction_store = TransactionStore(project_dir)

        report = run_dsl_privatize(store, transaction_store, project_dir)

        assert report.outcome.summary is not None
        assert (project_dir / "src" / "dead.py").read_text() == ORPHAN
