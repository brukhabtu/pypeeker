"""Direct unit tests for :mod:`pypeeker.app.intent_fixes` (no CliRunner, no engine).

``plan_intent_fixes`` is the engine-agnostic half of ``check --fix``: a flat
list of :class:`~pypeeker.intents.Intent` in, one ``check-fix`` transaction
out. It is a deliberate re-implementation of ``app/check_fixes.py``'s single
pass rather than a call into it (the new side must never execute frozen-path
code, or the oracle grades a thing against itself), so the behaviour it copies
has to be pinned here independently — a shared test would not notice the two
drifting apart, which is exactly what these tests exist to prevent.

Everything is exercised through :class:`~pypeeker.intents.ReplaceTextIntent`:
the one planner-backed kind that needs neither a rule nor an indexed symbol to
construct, which keeps these tests about the pass and not about any rule.
"""

from __future__ import annotations

import pytest

from pypeeker.app import DuplicateIntentIdError, IntentFixOutcome, plan_intent_fixes
from pypeeker.intents import ReplaceTextIntent
from pypeeker.storage import TransactionStore


def _replace(fix_id: str, file_path: str, old_text: str, new_text: str) -> ReplaceTextIntent:
    """A ReplaceTextIntent anchored purely by unique-text search (line/col unused)."""
    return ReplaceTextIntent(fix_id, file_path, 0, 0, old_text, new_text)


class TestApplied:
    """The ordinary path: repairs plan, land as one transaction, and apply."""

    def test_applies_in_deterministic_file_and_offset_order(self, indexed_project):
        project_dir, store = indexed_project(
            {"b.py": "TARGET_B = 1\n", "a.py": "TARGET_A = 1\n"}
        )
        transaction_store = TransactionStore(project_dir)
        # Fed in an order that is neither file-sorted nor id-sorted. The pass
        # imposes (file, first edit start, intent_id), which is what makes the
        # outcome a function of the intent SET rather than of its arrival order
        # — the contract that lets `Application.intents` stay unordered.
        outcome = plan_intent_fixes(
            store,
            transaction_store,
            [
                _replace("z-fix", "b.py", "TARGET_B", "B_DONE"),
                _replace("a-fix", "a.py", "TARGET_A", "A_DONE"),
            ],
        )

        assert [entry["fix_id"] for entry in outcome.fixes] == ["a-fix", "z-fix"]
        assert (project_dir / "a.py").read_text() == "A_DONE = 1\n"
        assert (project_dir / "b.py").read_text() == "B_DONE = 1\n"
        assert outcome.apply_result is not None
        assert outcome.apply_result["files_modified"] == ["a.py", "b.py"]

    def test_the_report_entry_carries_the_intents_own_description(
        self, indexed_project
    ):
        # `description` is the planner-facing prose the frozen report prints
        # verbatim; the oracle compares it per fix, so it must come off the
        # intent rather than be composed here.
        project_dir, store = indexed_project({"mod.py": "TARGET = 1\n"})
        intent = _replace("a-fix", "mod.py", "TARGET", "DONE")

        outcome = plan_intent_fixes(
            store, TransactionStore(project_dir), [intent]
        )

        assert outcome.fixes == [
            {"fix_id": "a-fix", "description": intent.description}
        ]

    def test_only_the_combined_transaction_reaches_the_project(self, indexed_project):
        # Every planner persists the transaction it plans; those go to a
        # throwaway temp store, so exactly one check-fix transaction lands.
        project_dir, store = indexed_project(
            {"a.py": "TARGET_A = 1\n", "b.py": "TARGET_B = 1\n"}
        )
        transaction_store = TransactionStore(project_dir)

        outcome = plan_intent_fixes(
            store,
            transaction_store,
            [
                _replace("a-fix", "a.py", "TARGET_A", "A_DONE"),
                _replace("b-fix", "b.py", "TARGET_B", "B_DONE"),
            ],
        )

        assert transaction_store.list() == [outcome.tx_id]
        assert transaction_store.load(outcome.tx_id).header.operation == "check-fix"

    def test_nothing_to_fix_writes_no_transaction(self, indexed_project):
        project_dir, store = indexed_project({"mod.py": "TARGET = 1\n"})
        transaction_store = TransactionStore(project_dir)

        outcome = plan_intent_fixes(store, transaction_store, [])

        assert outcome == IntentFixOutcome()
        assert transaction_store.list() == []


class TestPlanOnly:
    """``plan_only`` writes the transaction PENDING and touches no file."""

    def test_plan_only_persists_the_transaction_and_applies_nothing(
        self, indexed_project
    ):
        project_dir, store = indexed_project({"mod.py": "TARGET = 1\n"})
        transaction_store = TransactionStore(project_dir)

        outcome = plan_intent_fixes(
            store,
            transaction_store,
            [_replace("a-fix", "mod.py", "TARGET", "DONE")],
            plan_only=True,
        )

        assert [entry["fix_id"] for entry in outcome.fixes] == ["a-fix"]
        assert outcome.apply_result is None
        assert (project_dir / "mod.py").read_text() == "TARGET = 1\n"

        loaded = transaction_store.load(outcome.tx_id)
        assert loaded is not None
        assert loaded.header.operation == "check-fix"
        assert loaded.header.status.value == "pending"
        assert [(e.file, e.new) for e in loaded.edits] == [("mod.py", "DONE")]


class TestConflicts:
    """Two repairs whose byte ranges overlap: one lands, the other is skipped."""

    def test_overlapping_repair_is_skipped_not_applied(self, indexed_project):
        project_dir, store = indexed_project({"mod.py": "count = 100\n"})
        transaction_store = TransactionStore(project_dir)
        # "a-whole" replaces the entire assignment; "b-narrow" replaces just
        # the "100" inside it — their byte ranges overlap.
        outcome = plan_intent_fixes(
            store,
            transaction_store,
            [
                _replace("a-whole", "mod.py", "count = 100", "count = 999"),
                _replace("b-narrow", "mod.py", "100", "200"),
            ],
        )

        assert [entry["fix_id"] for entry in outcome.fixes] == ["a-whole"]
        assert [entry["fix_id"] for entry in outcome.skipped_conflicts] == ["b-narrow"]
        assert (project_dir / "mod.py").read_text() == "count = 999\n"

    def test_the_conflict_winner_is_the_ordering_key_not_the_arrival_order(
        self, indexed_project
    ):
        # Same two repairs, reversed on input: the winner does not change,
        # because the pass sorts before it de-conflicts. This is what makes
        # comparing the ORDERED fixes list a real grade of the de-conflict.
        project_dir, store = indexed_project({"mod.py": "count = 100\n"})

        outcome = plan_intent_fixes(
            store,
            TransactionStore(project_dir),
            [
                _replace("b-narrow", "mod.py", "100", "200"),
                _replace("a-whole", "mod.py", "count = 100", "count = 999"),
            ],
        )

        assert [entry["fix_id"] for entry in outcome.fixes] == ["a-whole"]
        assert [entry["fix_id"] for entry in outcome.skipped_conflicts] == ["b-narrow"]

    def test_non_overlapping_repairs_in_one_file_both_land(self, indexed_project):
        project_dir, store = indexed_project({"mod.py": "FIRST = 1\nSECOND = 2\n"})

        outcome = plan_intent_fixes(
            store,
            TransactionStore(project_dir),
            [
                _replace("a-fix", "mod.py", "FIRST", "ONE"),
                _replace("b-fix", "mod.py", "SECOND", "TWO"),
            ],
        )

        assert [entry["fix_id"] for entry in outcome.fixes] == ["a-fix", "b-fix"]
        assert outcome.skipped_conflicts == []
        assert (project_dir / "mod.py").read_text() == "ONE = 1\nTWO = 2\n"


class TestDeclined:
    """A planner refusal is a declined entry carrying that planner's own code."""

    def test_missing_anchor_text_is_declined(self, indexed_project):
        project_dir, store = indexed_project({"mod.py": "x = 1\n"})
        transaction_store = TransactionStore(project_dir)

        outcome = plan_intent_fixes(
            store,
            transaction_store,
            [_replace("d-fix", "mod.py", "NOPE_NOT_HERE", "y")],
        )

        assert outcome.fixes == []
        assert outcome.declined == [
            {
                "fix_id": "d-fix",
                "reason": "text-mismatch",
                "detail": outcome.declined[0]["detail"],
            }
        ]
        assert outcome.declined[0]["detail"]
        assert outcome.tx_id is None

    def test_ambiguous_anchor_keeps_its_own_code(self, indexed_project):
        # Distinct codes stay distinct: a single generic slug would collapse
        # `ambiguous` and `text-mismatch`, and the oracle compares the pair.
        project_dir, store = indexed_project({"mod.py": "x = 1\ny = 1\n"})

        outcome = plan_intent_fixes(
            store,
            TransactionStore(project_dir),
            [_replace("a-fix", "mod.py", "= 1", "= 2")],
        )

        assert [(e["fix_id"], e["reason"]) for e in outcome.declined] == [
            ("a-fix", "ambiguous")
        ]

    def test_a_refusal_does_not_stop_the_other_repairs(self, indexed_project):
        project_dir, store = indexed_project({"a.py": "TARGET_A = 1\n", "b.py": "x = 1\n"})

        outcome = plan_intent_fixes(
            store,
            TransactionStore(project_dir),
            [
                _replace("a-fix", "a.py", "TARGET_A", "A_DONE"),
                _replace("d-fix", "b.py", "NOPE_NOT_HERE", "y"),
            ],
        )

        assert [entry["fix_id"] for entry in outcome.fixes] == ["a-fix"]
        assert [entry["fix_id"] for entry in outcome.declined] == ["d-fix"]
        assert (project_dir / "a.py").read_text() == "A_DONE = 1\n"


class TestDuplicateIds:
    """Two repairs deriving one id is a refusal, never a phantom conflict."""

    def test_a_repeated_intent_id_is_refused_by_name(self, indexed_project):
        project_dir, store = indexed_project({"mod.py": "FIRST = 1\nSECOND = 2\n"})

        with pytest.raises(DuplicateIntentIdError) as excinfo:
            plan_intent_fixes(
                store,
                TransactionStore(project_dir),
                [
                    _replace("same-id", "mod.py", "FIRST", "ONE"),
                    _replace("same-id", "mod.py", "SECOND", "TWO"),
                ],
            )

        assert "same-id" in str(excinfo.value)

    def test_the_refusal_happens_before_anything_is_planned(self, indexed_project):
        # Refused up front, so no half-written transaction and no file touched
        # — the duplicate is an authoring bug, not a partial outcome.
        project_dir, store = indexed_project({"mod.py": "FIRST = 1\nSECOND = 2\n"})
        transaction_store = TransactionStore(project_dir)

        with pytest.raises(DuplicateIntentIdError):
            plan_intent_fixes(
                store,
                transaction_store,
                [
                    _replace("same-id", "mod.py", "FIRST", "ONE"),
                    _replace("same-id", "mod.py", "SECOND", "TWO"),
                ],
            )

        assert transaction_store.list() == []
        assert (project_dir / "mod.py").read_text() == "FIRST = 1\nSECOND = 2\n"
