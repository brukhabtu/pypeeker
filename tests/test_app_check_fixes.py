"""Direct unit tests for :mod:`pypeeker.app.check_fixes` (no CliRunner).

The whole point of extracting ``apply_check_fixes`` out of ``cli.py`` into
``app`` is that this workflow — deterministic ordering, overlap/conflict
resolution, apply, and residual re-check — is testable by calling it
directly. Real end-to-end coverage of concrete rule fixes (prefer-tuple,
unused-imports, ...) stays in ``tests/test_check_fix.py``; this file only
exercises ``apply_check_fixes``'s own planning/ordering/conflict/error logic,
using :class:`~pypeeker.check.fixes.ReplaceTextFix` (a minimal, concrete
``Fix``) and a tiny fake ``Fix`` for the apply-failure path.
"""

from __future__ import annotations

from dataclasses import dataclass

from pypeeker.app.check_fixes import CheckFixApplyError, apply_check_fixes
from pypeeker.check import CheckConfig, CheckEngine
from pypeeker.check.fixes import FixPlan, ReplaceTextFix
from pypeeker.check.models import Violation
from pypeeker.models.capabilities import Confidence
from pypeeker.models.transaction import EditEntry, EditOp
from pypeeker.storage import TransactionStore


def _violation(
    file_path: str,
    line: int,
    message: str,
    fix,
    confidence: Confidence = Confidence.DECLARED,
) -> Violation:
    """A minimal Violation carrying ``fix``, for exercising the workflow directly."""
    return Violation(
        file_path=file_path,
        line=line,
        rule="synthetic-rule",
        message=message,
        confidence=confidence,
        fix=fix,
    )


def _replace_fix(fix_id: str, file_path: str, old_text: str, new_text: str):
    """A ReplaceTextFix anchored purely by unique-text search (line/column unused)."""
    return ReplaceTextFix(fix_id, f"replace {old_text!r}", file_path, 0, 0, old_text, new_text)


def _no_op_engine(store) -> CheckEngine:
    """A CheckEngine with no rules enabled, so ``.run()`` always returns []."""
    return CheckEngine(store, CheckConfig())


@dataclass(frozen=True)
class _BadHashFix:
    """A fake Fix that plans a valid-looking edit with a deliberately wrong hash.

    Used only to force :class:`~pypeeker.refactor.applier.ApplyError` (hash
    mismatch) without racing the real filesystem, so
    :func:`~pypeeker.app.check_fixes.apply_check_fixes` can be observed
    raising :class:`~pypeeker.app.check_fixes.CheckFixApplyError`.
    """

    fix_id: str
    description: str
    file_path: str

    def plan(self, store):
        """Return a FixPlan whose file_hash never matches the real file."""
        return FixPlan(
            self.fix_id,
            self.description,
            [
                EditEntry(
                    op=EditOp.REPLACE,
                    file=self.file_path,
                    start=0,
                    end=1,
                    old="x",
                    new="y",
                    file_hash="0" * 64,
                )
            ],
        )


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
            _violation("b.py", 1, "b", _replace_fix("z-fix", "b.py", "TARGET_B", "B_DONE")),
            _violation("a.py", 1, "a", _replace_fix("a-fix", "a.py", "TARGET_A", "A_DONE")),
        ]

        outcome = apply_check_fixes(
            store, transaction_store, _no_op_engine(store), violations
        )

        assert [entry["fix_id"] for entry in outcome.applied] == ["a-fix", "z-fix"]
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
            "mod.py", 1, "whole", _replace_fix("a-whole", "mod.py", "count = 100", "count = 999")
        )
        narrow = _violation(
            "mod.py", 1, "narrow", _replace_fix("b-narrow", "mod.py", "100", "200")
        )

        outcome = apply_check_fixes(
            store, transaction_store, _no_op_engine(store), [whole, narrow]
        )

        assert [entry["fix_id"] for entry in outcome.applied] == ["a-whole"]
        assert [entry["fix_id"] for entry in outcome.skipped_conflicts] == ["b-narrow"]
        assert (project_dir / "mod.py").read_text() == "count = 999\n"


class TestConfidenceGate:
    """Only DECLARED-confidence findings ever auto-fix."""

    def test_heuristic_confidence_fix_is_never_planned(self, indexed_project):
        project_dir, store = indexed_project({"mod.py": "TARGET = 1\n"})
        transaction_store = TransactionStore(project_dir)
        violation = _violation(
            "mod.py",
            1,
            "heuristic",
            _replace_fix("h-fix", "mod.py", "TARGET", "CHANGED"),
            confidence=Confidence.HEURISTIC,
        )

        outcome = apply_check_fixes(
            store, transaction_store, _no_op_engine(store), [violation]
        )

        assert outcome.applied == []
        assert outcome.declined == []
        assert outcome.tx_id is None
        assert outcome.residual == [violation]
        assert (project_dir / "mod.py").read_text() == "TARGET = 1\n"


class TestDeclinedFix:
    """A fix that cannot re-anchor is reported as declined, not applied."""

    def test_missing_anchor_text_is_declined(self, indexed_project):
        project_dir, store = indexed_project({"mod.py": "x = 1\n"})
        transaction_store = TransactionStore(project_dir)
        violation = _violation(
            "mod.py", 1, "gone", _replace_fix("d-fix", "mod.py", "NOPE_NOT_HERE", "y")
        )

        outcome = apply_check_fixes(
            store, transaction_store, _no_op_engine(store), [violation]
        )

        assert outcome.applied == []
        assert len(outcome.declined) == 1
        assert outcome.declined[0]["fix_id"] == "d-fix"
        assert outcome.declined[0]["reason"] == "text-mismatch"
        assert outcome.tx_id is None


class TestApplyFailure:
    """A hash-mismatched plan surfaces as CheckFixApplyError, not a silent write."""

    def test_apply_error_is_raised_as_check_fix_apply_error(self, indexed_project):
        project_dir, store = indexed_project({"mod.py": "x = 1\n"})
        transaction_store = TransactionStore(project_dir)
        violation = _violation(
            "mod.py", 1, "bad-hash", _BadHashFix("bad-fix", "bad hash fix", "mod.py")
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
