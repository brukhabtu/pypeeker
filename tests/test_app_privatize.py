"""Direct tests for :mod:`pypeeker.app.privatize` (TASK-157).

``run_privatize`` is the ``privatize`` workflow behind the CLI. The frozen
``app/privatize.py`` service's report on each fixture below was captured before
it was deleted, and :data:`FROZEN_REPORTS` records it — so the scenarios in
:class:`TestParityWithTheFrozenService` still assert what the old service said,
field for field, rather than merely what this one says.

The two engines differ in exactly one observable: the derived intent id, which
fork #5 now prefixes with the origin rule. That difference is asserted from
both sides, and ``dsl-rewrite.md``'s divergence ledger carries it.
"""

from __future__ import annotations

import pytest

from pypeeker.app.privatize import PrivatizeReport, run_privatize
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


_NAME_COLLISION_DETAIL = "the target scope already binds '_helper'"
_HEURISTIC_DETAIL = (
    "the nominating finding has heuristic confidence (dynamic access nearby); "
    "excluded from auto-fix"
)
_EMPTY_REPORT = {
    "executed": [],
    "skipped": [],
    "dropped": [],
    "warnings": [],
    "files_affected": [],
    "edit_count": 0,
}

FROZEN_REPORTS = {
    "duplicate-nomination": {
        "executed": [("src.dead:orphan", "_orphan")],
        "skipped": [
            (
                "src.dead:orphan",
                "pending-collision",
                "'src.dead:orphan' earlier in this batch already demotes to "
                "'_orphan' in the same scope",
            )
        ],
        "dropped": [],
        "warnings": [],
        "files_affected": ["src/dead.py"],
        "edit_count": 1,
    },
    "interleaved-skips": {
        "executed": [],
        "skipped": [
            ("src.a:helper", "name-collision", _NAME_COLLISION_DETAIL),
            ("src.a:helper", "name-collision", _NAME_COLLISION_DETAIL),
            ("src.z:spooky", "heuristic-confidence", _HEURISTIC_DETAIL),
            ("src.z:spooky", "heuristic-confidence", _HEURISTIC_DETAIL),
        ],
        "dropped": [],
        "warnings": [],
        "files_affected": [],
        "edit_count": 0,
    },
    "barrel-export": _EMPTY_REPORT,
    "nothing-plannable": _EMPTY_REPORT,
    "main-and-private": _EMPTY_REPORT,
}
"""``_report(...)`` of the frozen service on each fixture below.

Captured from ``app/privatize.py`` at the flip, before it was deleted. These
are the ``privatize`` CLI's JSON fields, so a change to any of them is a
user-visible report change.
"""


class TestParityWithTheFrozenService:
    def test_duplicate_nomination_reports_pending_collision_on_both(
        self, indexed_project
    ):
        # One unreferenced public function is nominated by BOTH
        # over-exposed-module-symbol and unused-public-symbol, so two intents
        # arrive for one symbol. The frozen service reported the second as
        # pending-collision; so must this one, or the report changes shape on
        # essentially every real project.
        project_dir, store = _project(indexed_project, {"dead.py": ORPHAN})
        transaction_store = TransactionStore(project_dir)

        fresh = run_privatize(store, transaction_store, project_dir)

        assert _report(fresh.outcome) == FROZEN_REPORTS["duplicate-nomination"]
        assert [s.reason for s in fresh.outcome.skipped] == ["pending-collision"]
        # The one difference is fork #5's derived id, already in the ledger: the
        # frozen service spelled it `demote:src.dead:orphan`; the origin rule
        # now prefixes it.
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

        fresh = run_privatize(store, transaction_store, project_dir)

        # Two nominations per symbol (over-exposed + unused-public), so four
        # rows: both pre-filter skips first, both mutation refusals after.
        assert [(s.symbol_id, s.reason) for s in fresh.outcome.skipped] == [
            ("src.a:helper", "name-collision"),
            ("src.a:helper", "name-collision"),
            ("src.z:spooky", "heuristic-confidence"),
            ("src.z:spooky", "heuristic-confidence"),
        ]
        assert _report(fresh.outcome) == FROZEN_REPORTS["interleaved-skips"]

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

        fresh = run_privatize(store, transaction_store, project_dir)

        assert _report(fresh.outcome) == FROZEN_REPORTS["barrel-export"]
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

        fresh = run_privatize(store, transaction_store, project_dir)

        assert fresh.outcome.summary is None
        assert fresh.outcome.executed == []
        assert _report(fresh.outcome) == FROZEN_REPORTS["nothing-plannable"]

    def test_main_and_private_symbols_are_never_demoted(self, indexed_project):
        project_dir, store = _project(
            indexed_project,
            {"cli.py": "def main():\n    pass\n\n\ndef _helper():\n    pass\n"},
        )
        transaction_store = TransactionStore(project_dir)

        fresh = run_privatize(store, transaction_store, project_dir)

        assert fresh.outcome.executed == []
        assert fresh.outcome.summary is None
        # Neither name is even nominated: _candidate_clauses filters main and
        # non-public symbols out of the *selection*, so no skip row appears
        # either — which is what the frozen engine did too.
        assert fresh.outcome.skipped == []
        assert _report(fresh.outcome) == FROZEN_REPORTS["main-and-private"]


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

        report = run_privatize(store, transaction_store, project_dir)

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
        # rules; run_privatize calls it once per rule with that rule's own
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

        report = run_privatize(store, transaction_store, project_dir)

        # Still demoted, but now nominated only by unused-public-symbol, so
        # there is no duplicate and no pending-collision row.
        assert [e.intent_id for e in report.outcome.executed] == [
            "unused-public-symbol:demote:src.dead:orphan"
        ]
        assert report.outcome.skipped == []

    def test_selecting_a_single_rule_restricts_the_run(self, indexed_project):
        project_dir, store = _project(indexed_project, {"dead.py": ORPHAN})
        transaction_store = TransactionStore(project_dir)

        report = run_privatize(
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
            run_privatize(
                store, transaction_store, project_dir, ("unused-imports",)
            )


class TestReportShape:
    def test_the_report_is_public_carries_only_the_outcome_and_is_barrel_exported(
        self, indexed_project
    ):
        import dataclasses

        from pypeeker import app

        assert app.PrivatizeReport is PrivatizeReport
        assert app.run_privatize is run_privatize
        assert [f.name for f in dataclasses.fields(PrivatizeReport)] == ["outcome"]
        assert "PrivatizeReport" in app.__all__
        assert "run_privatize" in app.__all__

    def test_run_privatize_takes_no_apply_plan_and_never_writes_the_tree(
        self, indexed_project
    ):
        import inspect

        signature = inspect.signature(run_privatize)
        assert list(signature.parameters) == [
            "store",
            "transaction_store",
            "root",
            "rules",
        ]

        project_dir, store = _project(indexed_project, {"dead.py": ORPHAN})
        transaction_store = TransactionStore(project_dir)

        report = run_privatize(store, transaction_store, project_dir)

        assert report.outcome.summary is not None
        assert (project_dir / "src" / "dead.py").read_text() == ORPHAN
