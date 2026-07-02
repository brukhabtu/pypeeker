"""Direct unit tests for :mod:`pypeeker.app.privatize` (no CliRunner).

``run_privatize`` is the ``privatize`` CLI command's workflow: run the
demotion-feeding check rules, extract nominated symbols, and plan (and
optionally apply) one batch demotion. Extracting it out of ``cli.py`` makes
it directly testable; end-to-end coverage of the CLI's JSON shape and exit
codes stays in ``tests/test_privatize_cli.py``.
"""

from __future__ import annotations

from pypeeker.app.privatize import run_privatize
from pypeeker.storage import TransactionStore


def _project(indexed_project, files: dict[str, str]):
    """An indexed_project rooted so demotion-feeding rules see src/*.py."""
    return indexed_project({f"src/{name}": content for name, content in files.items()})


class TestPlanOnly:
    def test_orphan_symbol_is_planned_but_tree_is_untouched(self, indexed_project):
        project_dir, store = _project(
            indexed_project, {"dead.py": "def orphan():\n    return 1\n"}
        )
        transaction_store = TransactionStore(project_dir)

        report = run_privatize(store, transaction_store, project_dir, ())

        assert report.outcome.summary is not None
        assert [e.symbol_id for e in report.outcome.executed] == ["src.dead:orphan"]
        assert report.applied is None
        assert report.apply_error is None
        # Plan-only: the real file on disk is untouched.
        assert (project_dir / "src" / "dead.py").read_text() == (
            "def orphan():\n    return 1\n"
        )

    def test_no_candidates_yields_no_summary(self, indexed_project):
        project_dir, store = _project(
            indexed_project,
            {"app.py": "from src.dead import used\n\nused()\n", "dead.py": "def used():\n    return 1\n"},
        )
        transaction_store = TransactionStore(project_dir)

        report = run_privatize(store, transaction_store, project_dir, ())

        assert report.outcome.summary is None
        assert report.outcome.executed == []


class TestApply:
    def test_apply_plan_writes_the_demotion(self, indexed_project):
        project_dir, store = _project(
            indexed_project, {"dead.py": "def orphan():\n    return 1\n"}
        )
        transaction_store = TransactionStore(project_dir)

        report = run_privatize(
            store, transaction_store, project_dir, (), apply_plan=True
        )

        assert report.outcome.summary is not None
        assert report.apply_error is None
        assert report.applied is not None
        assert report.applied["status"] == "applied"
        assert (project_dir / "src" / "dead.py").read_text() == (
            "def _orphan():\n    return 1\n"
        )


class TestRuleSelectionAndHeuristics:
    def test_selecting_a_single_rule_restricts_the_run(self, indexed_project):
        project_dir, store = _project(
            indexed_project, {"dead.py": "def orphan():\n    return 1\n"}
        )
        transaction_store = TransactionStore(project_dir)

        report = run_privatize(
            store,
            transaction_store,
            project_dir,
            ("test-only-production-code",),
        )

        # orphan() has zero references at all, so it is not test-only-only;
        # the narrowly-selected rule finds nothing to demote.
        assert report.outcome.summary is None

    def test_heuristic_confidence_finding_is_skipped_by_default(self, indexed_project):
        project_dir, store = _project(
            indexed_project,
            {
                "dyn.py": (
                    "def ghost():\n"
                    "    return 1\n"
                    "\n"
                    "\n"
                    'value = getattr(object, "x", None)\n'
                )
            },
        )
        transaction_store = TransactionStore(project_dir)

        report = run_privatize(store, transaction_store, project_dir, ())

        assert report.outcome.summary is None
        assert {s.reason for s in report.outcome.skipped} == {"heuristic-confidence"}

        included = run_privatize(
            store, transaction_store, project_dir, (), skip_heuristic=False
        )
        assert included.outcome.summary is not None
        assert [e.symbol_id for e in included.outcome.executed] == ["src.dyn:ghost"]
