"""Direct unit tests for :mod:`pypeeker.app.check_fixes` (no CliRunner).

The whole point of extracting ``apply_check_fixes`` out of ``cli.py`` into
``app`` is that this workflow — deterministic ordering, overlap/conflict
resolution, apply, and residual re-check — is testable by calling it
directly. Real end-to-end coverage of concrete rule remedies (prefer-tuple,
unused-imports, ...) stays in ``tests/test_check_fix.py``; this file only
exercises ``apply_check_fixes``'s own submission/ordering/conflict/error
logic, using :class:`~pypeeker.intents.ReplaceTextIntent` (the ported
reference text op — the one planner-backed kind that needs neither a rule nor
an indexed symbol to construct) and a test-only intent kind for the
apply-failure path.
"""

from __future__ import annotations

import dataclasses
from typing import ClassVar

import pytest

from pypeeker.app.check_fixes import CheckFixApplyError, apply_check_fixes
from pypeeker.check import CheckConfig, CheckEngine
from pypeeker.check.models import Violation
from pypeeker.intents import EMPTY_EFFECT, EMPTY_FOOTPRINT, Intent, ReplaceTextIntent
from pypeeker.models.capabilities import Confidence
from pypeeker.models.transaction import EditEntry, EditOp
from pypeeker.refactor import registry as planner_registry
from pypeeker.refactor.registry import Materialized, register_planner
from pypeeker.storage import TransactionStore


def _violation(
    file_path: str,
    line: int,
    message: str,
    remedy,
    confidence: Confidence = Confidence.DECLARED,
) -> Violation:
    """A minimal Violation carrying ``remedy``, for exercising the workflow."""
    return Violation(
        file_path=file_path,
        line=line,
        rule="synthetic-rule",
        message=message,
        confidence=confidence,
        remedy=remedy,
    )


def _replace(fix_id: str, file_path: str, old_text: str, new_text: str):
    """A ReplaceTextIntent anchored purely by unique-text search (line/col unused)."""
    return ReplaceTextIntent(fix_id, file_path, 0, 0, old_text, new_text)


def _no_op_engine(store) -> CheckEngine:
    """A CheckEngine with no rules enabled, so ``.run()`` always returns []."""
    return CheckEngine(store, CheckConfig())


@dataclasses.dataclass(frozen=True)
class _BadHashIntent(Intent):
    """A test-only intent whose materializer emits a deliberately wrong hash.

    Used only to force :class:`~pypeeker.refactor.applier.ApplyError` (hash
    mismatch) without racing the real filesystem, so
    :func:`~pypeeker.app.check_fixes.apply_check_fixes` can be observed
    raising :class:`~pypeeker.app.check_fixes.CheckFixApplyError`.
    """

    file_path: str = ""

    kind: ClassVar[str] = "test-only:bad-hash"

    def footprint(self, store):
        """File-scoped, like every text-anchored intent."""
        return EMPTY_FOOTPRINT

    def predicted_effect(self, store):
        """Nothing predictable — the batch never gets this far."""
        return EMPTY_EFFECT

    def remap(self, effect):
        """Identity: the anchor never moves."""
        return self


@pytest.fixture(autouse=True)
def _bad_hash_materializer():
    """Register ``_BadHashIntent``'s materializer for the duration of a test."""

    @register_planner(_BadHashIntent.kind)
    def _materialize(intent, store, tx_store):
        return Materialized(
            edits=[
                EditEntry(
                    op=EditOp.REPLACE,
                    file=intent.file_path,
                    start=0,
                    end=1,
                    old="x",
                    new="y",
                    file_hash="0" * 64,
                )
            ]
        )

    yield
    planner_registry._REGISTRY.pop(_BadHashIntent.kind, None)


class TestPlanOnly:
    """``plan_only`` writes the transaction PENDING without touching a file."""

    def test_plan_only_persists_the_transaction_and_applies_nothing(
        self, indexed_project
    ):
        project_dir, store = indexed_project({"mod.py": "TARGET = 1\n"})
        transaction_store = TransactionStore(project_dir)
        violations = [
            _violation("mod.py", 1, "x", _replace("a-fix", "mod.py", "TARGET", "DONE"))
        ]

        outcome = apply_check_fixes(
            store,
            transaction_store,
            _no_op_engine(store),
            violations,
            plan_only=True,
        )

        # Planned and reported, but not applied.
        assert [entry["fix_id"] for entry in outcome.fixes] == ["a-fix"]
        assert outcome.apply_result is None
        assert (project_dir / "mod.py").read_text() == "TARGET = 1\n"
        # Residual is the untouched input set — the engine is never re-run.
        assert outcome.residual == violations

        # The one combined transaction is on disk and PENDING.
        loaded = transaction_store.load(outcome.tx_id)
        assert loaded is not None
        header, edits, _ = loaded
        assert header.operation == "check-fix"
        assert header.status.value == "pending"
        assert [edit.file for edit in edits] == ["mod.py"]

    def test_applied_run_keeps_the_applier_result(self, indexed_project):
        # The apply result is retained (not collapsed to a bool) so callers
        # can report files_reindex_failed instead of swallowing it.
        project_dir, store = indexed_project({"mod.py": "TARGET = 1\n"})
        transaction_store = TransactionStore(project_dir)
        violations = [
            _violation("mod.py", 1, "x", _replace("a-fix", "mod.py", "TARGET", "DONE"))
        ]

        outcome = apply_check_fixes(
            store, transaction_store, _no_op_engine(store), violations
        )

        assert outcome.apply_result is not None
        assert outcome.apply_result["files_modified"] == ["mod.py"]
        assert outcome.apply_result["files_reindex_failed"] == []


class TestOrderingAndConflicts:
    """Deterministic (file, start, fix_id) ordering and overlap skipping."""

    def test_applies_in_deterministic_file_and_offset_order(self, indexed_project):
        project_dir, store = indexed_project(
            {
                "b.py": "TARGET_B = 1\n",
                "a.py": "TARGET_A = 1\n",
            }
        )
        transaction_store = TransactionStore(project_dir)
        # Fed in an order that is neither file-sorted nor fix-id-sorted;
        # the outcome must still reflect (file, start, fix_id) order.
        violations = [
            _violation("b.py", 1, "b", _replace("z-fix", "b.py", "TARGET_B", "B_DONE")),
            _violation("a.py", 1, "a", _replace("a-fix", "a.py", "TARGET_A", "A_DONE")),
        ]

        outcome = apply_check_fixes(
            store, transaction_store, _no_op_engine(store), violations
        )

        assert [entry["fix_id"] for entry in outcome.fixes] == ["a-fix", "z-fix"]
        assert (project_dir / "a.py").read_text() == "A_DONE = 1\n"
        assert (project_dir / "b.py").read_text() == "B_DONE = 1\n"
        assert outcome.tx_id is not None
        assert outcome.residual == []  # re-run through the no-op engine

    def test_overlapping_fix_is_skipped_as_conflict(self, indexed_project):
        project_dir, store = indexed_project({"mod.py": "count = 100\n"})
        transaction_store = TransactionStore(project_dir)
        # "whole" replaces the entire assignment; "narrow" replaces just the
        # "100" substring inside it -- their byte ranges overlap.
        whole = _violation(
            "mod.py", 1, "whole", _replace("a-whole", "mod.py", "count = 100", "count = 999")
        )
        narrow = _violation(
            "mod.py", 1, "narrow", _replace("b-narrow", "mod.py", "100", "200")
        )

        outcome = apply_check_fixes(
            store, transaction_store, _no_op_engine(store), [whole, narrow]
        )

        assert [entry["fix_id"] for entry in outcome.fixes] == ["a-whole"]
        assert [entry["fix_id"] for entry in outcome.skipped_conflicts] == ["b-narrow"]
        assert (project_dir / "mod.py").read_text() == "count = 999\n"

    def test_only_the_combined_transaction_reaches_the_project(self, indexed_project):
        # Each remedy's planner persists its own transaction as a side effect
        # of planning; those go to a throwaway store, so `transactions list`
        # sees exactly one check-fix transaction per run.
        project_dir, store = indexed_project(
            {"a.py": "TARGET_A = 1\n", "b.py": "TARGET_B = 1\n"}
        )
        transaction_store = TransactionStore(project_dir)
        violations = [
            _violation("a.py", 1, "a", _replace("a-fix", "a.py", "TARGET_A", "A_DONE")),
            _violation("b.py", 1, "b", _replace("z-fix", "b.py", "TARGET_B", "B_DONE")),
        ]

        outcome = apply_check_fixes(
            store, transaction_store, _no_op_engine(store), violations
        )

        assert transaction_store.list() == [outcome.tx_id]
        loaded = transaction_store.load(outcome.tx_id)
        assert loaded is not None
        header, edits, _ = loaded
        assert header.operation == "check-fix"
        assert sorted(edit.file for edit in edits) == ["a.py", "b.py"]


class TestConfidenceGate:
    """Only DECLARED-confidence findings ever auto-fix."""

    def test_heuristic_confidence_fix_is_never_planned(self, indexed_project):
        project_dir, store = indexed_project({"mod.py": "TARGET = 1\n"})
        transaction_store = TransactionStore(project_dir)
        violation = _violation(
            "mod.py",
            1,
            "heuristic",
            _replace("h-fix", "mod.py", "TARGET", "CHANGED"),
            confidence=Confidence.HEURISTIC,
        )

        outcome = apply_check_fixes(
            store, transaction_store, _no_op_engine(store), [violation]
        )

        assert outcome.fixes == []
        assert outcome.declined == []
        assert outcome.tx_id is None
        assert outcome.residual == [violation]
        assert (project_dir / "mod.py").read_text() == "TARGET = 1\n"

    def test_violation_without_a_remedy_is_ignored(self, indexed_project):
        project_dir, store = indexed_project({"mod.py": "TARGET = 1\n"})
        transaction_store = TransactionStore(project_dir)
        violation = _violation("mod.py", 1, "no remedy", None)

        outcome = apply_check_fixes(
            store, transaction_store, _no_op_engine(store), [violation]
        )

        assert (outcome.fixes, outcome.declined, outcome.tx_id) == ([], [], None)


class TestDeclinedFix:
    """A remedy whose planner refuses is reported as declined, not applied."""

    def test_missing_anchor_text_is_declined(self, indexed_project):
        project_dir, store = indexed_project({"mod.py": "x = 1\n"})
        transaction_store = TransactionStore(project_dir)
        violation = _violation(
            "mod.py", 1, "gone", _replace("d-fix", "mod.py", "NOPE_NOT_HERE", "y")
        )

        outcome = apply_check_fixes(
            store, transaction_store, _no_op_engine(store), [violation]
        )

        assert outcome.fixes == []
        assert len(outcome.declined) == 1
        assert outcome.declined[0]["fix_id"] == "d-fix"
        assert outcome.declined[0]["reason"] == "text-mismatch"
        assert outcome.tx_id is None

    def test_ambiguous_anchor_is_declined_with_its_own_code(self, indexed_project):
        # The planner's refusal code — not a single generic slug — is what
        # reaches the report, so `ambiguous` and `text-mismatch` stay distinct.
        project_dir, store = indexed_project({"mod.py": "x = 1\ny = 1\n"})
        transaction_store = TransactionStore(project_dir)
        violation = _violation(
            "mod.py", 1, "twice", _replace("a-fix", "mod.py", "= 1", "= 2")
        )

        outcome = apply_check_fixes(
            store, transaction_store, _no_op_engine(store), [violation]
        )

        assert outcome.fixes == []
        assert outcome.declined[0]["reason"] == "ambiguous"
        assert (project_dir / "mod.py").read_text() == "x = 1\ny = 1\n"

    def test_missing_file_is_declined(self, indexed_project):
        project_dir, store = indexed_project({"mod.py": "x = 1\n"})
        transaction_store = TransactionStore(project_dir)
        violation = _violation(
            "gone.py", 1, "gone", _replace("f-fix", "gone.py", "x", "y")
        )

        outcome = apply_check_fixes(
            store, transaction_store, _no_op_engine(store), [violation]
        )

        assert outcome.declined[0]["reason"] == "file-missing"


class TestApplyFailure:
    """A hash-mismatched plan surfaces as CheckFixApplyError, not a silent write."""

    def test_apply_error_is_raised_as_check_fix_apply_error(self, indexed_project):
        project_dir, store = indexed_project({"mod.py": "x = 1\n"})
        transaction_store = TransactionStore(project_dir)
        violation = _violation(
            "mod.py", 1, "bad-hash", _BadHashIntent("bad-fix", file_path="mod.py")
        )

        try:
            apply_check_fixes(store, transaction_store, _no_op_engine(store), [violation])
            raised = False
        except CheckFixApplyError as e:
            raised = True
            assert e.tx_id is not None

        assert raised
        # The file is untouched -- the applier rolled back before raising.
        assert (project_dir / "mod.py").read_text() == "x = 1\n"
