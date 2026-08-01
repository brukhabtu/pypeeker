"""Tests for the TASK-123 single-execution-pipeline submit path.

``pypeeker.app.submit`` is what every single-intent mutating CLI command
routes through (see ``cli.py``'s ``rename``/``inline-variable``/
``extract-variable``/``extract-method``/``demote``/``promote``, renamed
from their old ``plan-*`` names by TASK-126, which also made them apply by
default). These tests exercise the submit path directly (not through
``CliRunner``), matching the project's existing split between
``test_promote_demote.py``'s CLI-surface tests and its "direct planner"
tests; the submit path itself is unchanged by TASK-126 — only cli.py's use
of its result (apply-or-not) changed, so this parity coverage keeps working
unmodified.
"""

from __future__ import annotations

import pytest

from pypeeker.app import SubmitError, submit_intent, submit_intents
from pypeeker.intents import ChangeVisibilityIntent, RenameIntent
from pypeeker.models import TransactionStatus, to_dict
from pypeeker.refactor import RenamePlanError, RenamePlanner
from pypeeker.refactor.batch import BatchResult, DropReason
from pypeeker.refactor.visibility_ops import (
    _DemoteError as DemoteError,
    VisibilityPlanner,
)

LIB = "def helper():\n    return 1\n"
APP_CALL = "from lib import helper\n\ndef use():\n    x = helper()\n    return x\n"


def _without_volatile(summary_dict: dict) -> dict:
    """A summary dict with the plan-time-only fields stripped for comparison."""
    return {k: v for k, v in summary_dict.items() if k not in ("tx_id", "created_at")}


# ---------------------------------------------------------------------------
# Single-intent submit: output parity with a direct planner call
# ---------------------------------------------------------------------------


class TestSingleIntentParity:
    def test_rename_matches_direct_planner_call(self, indexed_project, transaction_store):
        _, store = indexed_project({"lib.py": LIB, "app.py": APP_CALL})

        direct = RenamePlanner(store, transaction_store).plan("lib:helper", "assist")

        intent = RenameIntent("r1", "lib:helper", "assist")
        materialized = submit_intent(intent, store, transaction_store)

        assert materialized.summary is not None
        assert _without_volatile(to_dict(direct)) == _without_volatile(
            to_dict(materialized.summary)
        )

        header, _, _ = transaction_store.load(materialized.summary.tx_id)
        assert header.status is TransactionStatus.PENDING

    def test_extract_variable_matches_direct_planner_call(
        self, indexed_project, transaction_store
    ):
        from pypeeker.intents import ExtractVariableIntent
        from pypeeker.refactor import ExtractVariablePlanner

        _, store = indexed_project({"mod.py": "def f():\n    return foo(bar) + 2\n"})

        direct = ExtractVariablePlanner(store, transaction_store).plan(
            "mod.py", (1, 11), (1, 19), "value"
        )
        intent = ExtractVariableIntent("e1", "mod.py", (1, 11), (1, 19), "value")
        materialized = submit_intent(intent, store, transaction_store)

        assert materialized.summary is not None
        assert _without_volatile(to_dict(direct)) == _without_volatile(
            to_dict(materialized.summary)
        )

    def test_inline_variable_matches_direct_planner_call(
        self, indexed_project, transaction_store
    ):
        from pypeeker.intents import InlineVariableIntent
        from pypeeker.refactor import InlineVariablePlanner

        _, store = indexed_project({"mod.py": "def f():\n    x = 1\n    return x\n"})

        direct = InlineVariablePlanner(store, transaction_store).plan("mod:f:x")
        intent = InlineVariableIntent("i1", "mod:f:x")
        materialized = submit_intent(intent, store, transaction_store)

        assert materialized.summary is not None
        assert _without_volatile(to_dict(direct)) == _without_volatile(
            to_dict(materialized.summary)
        )

    def test_extract_method_matches_direct_planner_call(
        self, indexed_project, transaction_store
    ):
        from pypeeker.intents import ExtractMethodIntent
        from pypeeker.refactor import ExtractMethodPlanner

        _, store = indexed_project({"mod.py": "def f(a, b):\n    c = a + b\n    return c\n"})

        direct = ExtractMethodPlanner(store, transaction_store).plan(
            "mod.py", 1, 1, "add"
        )
        intent = ExtractMethodIntent("m1", "mod.py", 1, 1, "add")
        materialized = submit_intent(intent, store, transaction_store)

        assert materialized.summary is not None
        assert _without_volatile(to_dict(direct)) == _without_volatile(
            to_dict(materialized.summary)
        )


# ---------------------------------------------------------------------------
# Refusal parity: the same failing precondition surfaces the same error
# ---------------------------------------------------------------------------


class TestRefusalParity:
    def test_rename_refusal_matches_direct_planner_error(
        self, indexed_project, transaction_store
    ):
        _, store = indexed_project({"mod.py": "def foo(): pass\n\n\ndef bar(): pass\n"})

        with pytest.raises(RenamePlanError) as direct_error:
            RenamePlanner(store, transaction_store).plan("mod:foo", "bar")

        intent = RenameIntent("r1", "mod:foo", "bar")
        with pytest.raises(SubmitError) as submit_error:
            submit_intent(intent, store, transaction_store)

        assert submit_error.value.code == "plan-refused"
        assert submit_error.value.detail == str(direct_error.value)


# ---------------------------------------------------------------------------
# The scheduler really runs, even for a single intent
# ---------------------------------------------------------------------------


class TestSchedulerActuallyRuns:
    def test_single_intent_with_unknown_dep_is_rejected_at_schedule_time(
        self, indexed_project, transaction_store
    ):
        _, store = indexed_project({"mod.py": "def foo(): pass\n"})

        intent = RenameIntent("r1", "mod:foo", "bar", deps=frozenset({"no-such-id"}))
        with pytest.raises(SubmitError) as error:
            submit_intent(intent, store, transaction_store)

        assert error.value.code == "schedule-failed"
        assert "no-such-id" in error.value.detail
        # Scheduling failed before any materialization: nothing was planned.
        assert transaction_store.list() == []

    def test_multi_intent_pair_still_conflicts_through_submit_intents(
        self, indexed_project, transaction_store
    ):
        _, store = indexed_project({"lib.py": LIB, "app.py": APP_CALL})

        r1 = RenameIntent("r1", "lib:helper", "assist")
        r2 = RenameIntent("r2", "lib:helper", "do_help")
        result = submit_intents([r1, r2], store, transaction_store)

        assert isinstance(result, BatchResult)
        assert [i.intent.intent_id for i in result.executed] == ["r1"]
        (drop,) = result.dropped
        assert (drop.intent.intent_id, drop.reason) == ("r2", DropReason.CONFLICT_DROPPED)


# ---------------------------------------------------------------------------
# ChangeVisibilityIntent materializer dispatch, end-to-end
# ---------------------------------------------------------------------------


class TestChangeVisibilityIntentDispatch:
    def test_demote_matches_direct_planner_call(self, indexed_project, transaction_store):
        _, store = indexed_project(
            {
                "pkg/__init__.py": "from pkg.mod import helper\n",
                "pkg/mod.py": "def helper():\n    return 1\n",
                "app.py": "from pkg import helper\n\nhelper()\n",
            }
        )

        direct = VisibilityPlanner(store, transaction_store).plan_demote("pkg.mod:helper")

        intent = ChangeVisibilityIntent("v1", "pkg.mod:helper", "demote")
        materialized = submit_intent(intent, store, transaction_store)

        assert materialized.summary is not None
        assert _without_volatile(to_dict(direct.summary)) == _without_volatile(
            to_dict(materialized.summary)
        )
        assert materialized.warnings == direct.warnings
        assert direct.warnings  # sanity: this scenario does warn (barrel rewrite)

        header, _, _ = transaction_store.load(materialized.summary.tx_id)
        assert header.status is TransactionStatus.PENDING
        assert header.operation == "demote"

    def test_promote_matches_direct_planner_call(self, indexed_project, transaction_store):
        _, store = indexed_project({"mod.py": "def _solo():\n    pass\n\n\n_solo()\n"})

        direct = VisibilityPlanner(store, transaction_store).plan_promote("mod:_solo")

        intent = ChangeVisibilityIntent("v1", "mod:_solo", "promote")
        materialized = submit_intent(intent, store, transaction_store)

        assert materialized.summary is not None
        assert _without_volatile(to_dict(direct.summary)) == _without_volatile(
            to_dict(materialized.summary)
        )
        header, _, _ = transaction_store.load(materialized.summary.tx_id)
        assert header.operation == "promote"

    def test_demote_refusal_preserves_the_visibility_op_error_code(
        self, indexed_project, transaction_store
    ):
        _, store = indexed_project({"mod.py": "def _quiet():\n    pass\n"})

        with pytest.raises(DemoteError) as direct_error:
            VisibilityPlanner(store, transaction_store).plan_demote("mod:_quiet")

        intent = ChangeVisibilityIntent("v1", "mod:_quiet", "demote")
        with pytest.raises(SubmitError) as submit_error:
            submit_intent(intent, store, transaction_store)

        assert submit_error.value.code == direct_error.value.code == "already-private"
        assert submit_error.value.detail == str(direct_error.value)
        # The refusal happened before any transaction was persisted.
        assert transaction_store.list() == []

    def test_promote_export_target_refusal_leaves_no_transaction(
        self, indexed_project, transaction_store
    ):
        _, store = indexed_project({"mod.py": "def _quiet():\n    pass\n"})

        intent = ChangeVisibilityIntent(
            "v1", "mod:_quiet", "promote", add_export="nosuch"
        )
        with pytest.raises(SubmitError) as error:
            submit_intent(intent, store, transaction_store)

        assert error.value.code == "export-target"
        assert transaction_store.list() == []


# ---------------------------------------------------------------------------
# submit_intents edge cases
# ---------------------------------------------------------------------------


class TestSubmitIntentsEdgeCases:
    def test_empty_list_is_rejected(self, indexed_project, transaction_store):
        _, store = indexed_project({"mod.py": "x = 1\n"})
        with pytest.raises(SubmitError) as error:
            submit_intents([], store, transaction_store)
        assert error.value.code == "no-intents"

    def test_single_element_list_returns_a_materialized_not_a_batch_result(
        self, indexed_project, transaction_store
    ):
        """The return-type contract, not a fast path (TASK-129 renamed this).

        There is no longer a second execution path to take: a lone intent goes
        through ``run_batch`` like everything else. What a single-element list
        still selects is the *caller-facing contract* — the planner's own
        ``Materialized``, not a ``BatchResult`` — and these assertions are
        frozen exactly as they were before the collapse.
        """
        _, store = indexed_project({"mod.py": "def foo(): pass\n"})
        intent = RenameIntent("r1", "mod:foo", "bar")
        result = submit_intents([intent], store, transaction_store)
        assert not isinstance(result, BatchResult)
        assert result.summary is not None
        assert result.summary.new_name == "bar"
