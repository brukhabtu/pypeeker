"""The application services the thin CLI delegates to.

Each function here is an extraction from ``cli.py``: the command keeps its
options, JSON and exit codes, and the workflow moved behind a package barrel
where a programmatic caller (or a test) can reach it without Click. These
tests pin the plain-data contracts those callers see.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pypeeker.analysis import ContextError, purity_report
from pypeeker.app import (
    BoundaryConfigError,
    check_baseline_delta,
    dropped_intent_report,
    run_check,
    run_intent_batch,
    scratch_transactions,
    update_check_baseline,
)
from pypeeker.dsl import UnknownExpressionError, run_expression
from pypeeker.indexer import index_path
from pypeeker.intents import RenameIntent
from pypeeker.refactor import BatchPolicy
from pypeeker.storage import IndexStore, TransactionStore


def _pyproject(root: Path, body: str) -> None:
    (root / "pyproject.toml").write_text('[project]\nname = "t"\n' + body)


class TestScratchTransactions:
    def test_yields_a_store_that_dies_with_the_block(self):
        with scratch_transactions() as scratch:
            assert isinstance(scratch, TransactionStore)
            assert scratch.list() == []
            root = scratch.root
            assert "pypeeker-scratch-" in str(root)
        assert not root.exists()


class TestRunIntentBatch:
    def test_persists_one_flattened_transaction_in_the_callers_store(
        self, indexed_project, transaction_store
    ):
        _, store = indexed_project({"mod.py": "def foo(): pass\n"})
        payload = run_intent_batch(
            [RenameIntent("r1", "mod:foo", "bar")],
            store,
            transaction_store,
            policy=BatchPolicy.SKIP_AND_REPORT,
        )
        assert payload["executed"] == [{"id": "r1", "kind": "rename"}]
        assert payload["dropped"] == []
        assert payload["files_affected"] == ["mod.py"]
        assert payload["edit_count"] == 1
        assert payload["files_created"] == []
        assert payload["files_deleted"] == []
        # Exactly one durable transaction: the flattened batch, not the
        # per-intent re-plan (which went to scratch).
        assert transaction_store.list() == [payload["tx_id"]]

    def test_all_dropped_persists_nothing_and_reports_the_drops(
        self, indexed_project, transaction_store
    ):
        _, store = indexed_project({"mod.py": "def foo(): pass\n"})
        payload = run_intent_batch(
            [RenameIntent("r1", "mod:ghost", "bar")],
            store,
            transaction_store,
            policy=BatchPolicy.SKIP_AND_REPORT,
        )
        assert payload["executed"] == []
        assert payload["tx_id"] is None
        assert [d["id"] for d in payload["dropped"]] == ["r1"]
        assert set(payload["dropped"][0]) == {"id", "reason", "detail"}
        assert transaction_store.list() == []

    def test_dropped_intent_report_shape(self, indexed_project, transaction_store):
        _, store = indexed_project({"mod.py": "def foo(): pass\n"})
        from pypeeker.app import submit_intents

        result = submit_intents(
            [RenameIntent("r1", "mod:ghost", "bar")],
            store,
            transaction_store,
            always_batch=True,
        )
        (dropped,) = result.dropped
        assert dropped_intent_report(dropped) == {
            "id": "r1",
            "reason": dropped.reason.value,
            "detail": dropped.detail,
        }


def _checked_project(root: Path, files: dict[str, str], config: str) -> IndexStore:
    """The ``check`` layout: sources under ``src/``, indexed the way ``index`` does."""
    _pyproject(root, '[tool.pypeeker]\nsrc = ["src"]\n' + config)
    for name, content in files.items():
        path = root / "src" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    store = IndexStore(root)
    index_path(root / "src", store=store, root=root)
    return store


class TestRunCheck:
    def test_runs_the_configured_rules(self, tmp_path):
        store = _checked_project(
            tmp_path, {"mod.py": "import os\n"}, 'rules = ["unused-imports"]\n'
        )

        run = run_check(store, tmp_path)

        assert [v.rule for v in run.violations] == ["unused-imports"]
        assert run.engine is not None

    def test_refuses_a_nested_boundary_unit(self, tmp_path):
        store = _checked_project(
            tmp_path,
            {"mod.py": "x = 1\n"},
            'rules = ["import-boundaries"]\n'
            '[tool.pypeeker.import-boundaries]\nroot = "pkg"\n'
            "[tool.pypeeker.import-boundaries.allow]\n"
            '"a.b" = []\n',
        )
        with pytest.raises(BoundaryConfigError):
            run_check(store, tmp_path)

    def test_baseline_update_then_delta(self, tmp_path):
        root = tmp_path
        store = _checked_project(
            root, {"mod.py": "import os\n"}, 'rules = ["unused-imports"]\n'
        )
        violations = run_check(store, root).violations

        update = update_check_baseline(root, violations)
        assert update.recorded == 1
        assert update.path.is_relative_to(root)

        result = check_baseline_delta(root, violations)
        assert result.baselined == 1
        assert result.new == []
        assert result.fixed == []


class TestPurityReport:
    def test_plain_data_for_a_function(self, indexed_project):
        _, store = indexed_project({"mod.py": "def shout(m):\n    print(m)\n"})
        report = purity_report(store, "mod:shout")
        assert report == {
            "symbol_id": "mod:shout",
            "pure": False,
            "observations": [
                {**obs, "kind": "BareCall"} for obs in report["observations"]
            ],
        }
        assert report["observations"][0]["name"] == "print"

    def test_context_error_is_returned_not_raised(self, indexed_project):
        _, store = indexed_project({"mod.py": "x = 1\n"})
        report = purity_report(store, "mod:nope")
        assert isinstance(report, ContextError)
        assert report.reason == "not_found"


class TestRunExpression:
    def test_envelope_shape(self, indexed_project):
        _, store = indexed_project(
            {"mod.py": "def f():\n    xs = []\n    return xs[0]\n"}
        )
        report = run_expression(store, (), "tuple-candidate")
        assert set(report) == {"expression", "universe", "reach", "anchor", "results"}
        assert report["expression"] == "tuple-candidate"
        assert report["anchor"] is None
        assert [r["anchor"]["id"] for r in report["results"]] == ["mod:f:xs"]
        assert "why" not in report["results"][0]

    def test_why_attaches_a_derivation_document(self, indexed_project):
        _, store = indexed_project(
            {"mod.py": "def f():\n    xs = []\n    return xs[0]\n"}
        )
        report = run_expression(
            store, (), "tuple-candidate", anchor_id="mod:f:xs", why=True
        )
        assert report["anchor"] == "mod:f:xs"
        (result,) = report["results"]
        assert set(result["why"]) == {"schema", "derivations"}

    def test_unknown_expression_raises_the_dsl_error(self, indexed_project):
        _, store = indexed_project({"mod.py": "x = 1\n"})
        with pytest.raises(UnknownExpressionError):
            run_expression(store, (), "no-such-expression")
