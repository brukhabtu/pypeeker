"""Tests for the fix protocol (``pypeeker.check.fixes``).

Covers fix production end-to-end (plan -> TransactionStore -> Applier
round-trip), the decline paths (text-anchor mismatch, ambiguity, missing
file), re-planning after benign unrelated edits, and the no-fix regression
guarantees (equality / ordering / str / hash of plain Violations unchanged).

Violations are in-memory only: nothing in pypeeker serializes or persists
them (the engine returns them, the CLI prints ``str(v)``), so attaching an
arbitrary ``Fix`` object reference never needs to round-trip through JSON.
Only ``FixPlan.edits`` (plain EditEntry objects) ever hit disk — exercised
here via the TransactionStore round-trip.
"""

from __future__ import annotations

import dataclasses

from pypeeker.check import (
    DeclineReason,
    Fix,
    FixDeclined,
    FixPlan,
    ReplaceTextFix,
    Violation,
    with_fix,
)
from pypeeker.models.transaction import EditOp, TransactionHeader
from pypeeker.refactor.applier import TransactionApplier
from pypeeker.storage import IndexStore, TransactionStore

SOURCE = "def foo():\n    return 1\n"


def _foo_fix(**overrides) -> ReplaceTextFix:
    """Fix renaming the ``foo`` token at line 0, byte column 4 to ``bar``."""
    kwargs = dict(
        fix_id="test-rule:rename-foo",
        description="rename 'foo' to 'bar'",
        file_path="mod.py",
        line=0,
        column=4,
        old_text="foo",
        new_text="bar",
    )
    kwargs.update(overrides)
    return ReplaceTextFix(**kwargs)


class TestFixProduction:
    def test_plan_yields_edits_with_fresh_hashes(self, indexed_project):
        project_dir, store = indexed_project({"mod.py": SOURCE})

        plan = _foo_fix().plan(store)

        assert isinstance(plan, FixPlan)
        assert plan.fix_id == "test-rule:rename-foo"
        [edit] = plan.edits
        assert edit.file == "mod.py"
        assert edit.op is EditOp.REPLACE
        assert (edit.old, edit.new) == ("foo", "bar")
        content = (project_dir / "mod.py").read_bytes()
        assert content[edit.start : edit.end] == b"foo"
        assert edit.file_hash == IndexStore.compute_file_hash(project_dir / "mod.py")

    def test_plan_round_trips_through_transaction_applier(self, indexed_project):
        project_dir, store = indexed_project({"mod.py": SOURCE})
        plan = _foo_fix().plan(store)
        assert isinstance(plan, FixPlan)

        tx_store = TransactionStore(project_dir)
        header = TransactionHeader(
            tx_id="fix-tx-1",
            symbol_id="mod:foo",
            old_name="foo",
            new_name="bar",
            created_at="2026-06-11T00:00:00+00:00",
            operation="fix",
        )
        tx_store.save(header, plan.edits)
        result = TransactionApplier(store, tx_store).apply("fix-tx-1")

        assert result["status"] == "applied"
        assert (project_dir / "mod.py").read_text() == "def bar():\n    return 1\n"

    def test_anchor_match_wins_even_when_text_repeats(self, indexed_project):
        # "foo" occurs twice, but the recorded anchor still verifiably holds
        # the text, so the fix plans there with no ambiguity.
        project_dir, store = indexed_project({
            "mod.py": "def foo():\n    return 1\n\nfoo()\n"
        })

        plan = _foo_fix().plan(store)

        assert isinstance(plan, FixPlan)
        [edit] = plan.edits
        assert (edit.start, edit.end) == (4, 7)  # the def site, not the call


class TestFixDecline:
    def test_text_anchor_mismatch_declines(self, indexed_project):
        project_dir, store = indexed_project({"mod.py": SOURCE})
        fix = _foo_fix()
        # Mutate the file after detection: the expected text is gone entirely.
        (project_dir / "mod.py").write_text("def baz():\n    return 1\n")

        declined = fix.plan(store)

        assert isinstance(declined, FixDeclined)
        assert declined.reason is DeclineReason.TEXT_MISMATCH
        assert declined.fix_id == fix.fix_id
        assert "foo" in declined.detail

    def test_ambiguous_reanchor_declines(self, indexed_project):
        project_dir, store = indexed_project({"mod.py": SOURCE})
        fix = _foo_fix()
        # The anchor location no longer holds "foo" and the text now occurs
        # twice, so re-anchoring would be a guess: decline.
        (project_dir / "mod.py").write_text("# foo\n\ndef foo():\n    return 1\n")

        declined = fix.plan(store)

        assert isinstance(declined, FixDeclined)
        assert declined.reason is DeclineReason.AMBIGUOUS

    def test_missing_file_declines(self, indexed_project):
        project_dir, store = indexed_project({"mod.py": SOURCE})
        fix = _foo_fix()
        (project_dir / "mod.py").unlink()

        declined = fix.plan(store)

        assert isinstance(declined, FixDeclined)
        assert declined.reason is DeclineReason.FILE_MISSING


class TestReplanning:
    def test_benign_unrelated_edit_replans_against_new_state(
        self, indexed_project
    ):
        project_dir, store = indexed_project({"mod.py": SOURCE})
        fix = _foo_fix()
        stale_plan = fix.plan(store)
        assert isinstance(stale_plan, FixPlan)

        # Unrelated edit above the anchor shifts every byte offset.
        (project_dir / "mod.py").write_text(
            "import os\n\n" + SOURCE
        )
        plan = fix.plan(store)

        assert isinstance(plan, FixPlan)
        [edit] = plan.edits
        content = (project_dir / "mod.py").read_bytes()
        # Offsets and hash are for THE CURRENT state, not cached from
        # detection time.
        assert content[edit.start : edit.end] == b"foo"
        assert edit.start != stale_plan.edits[0].start
        assert edit.file_hash != stale_plan.edits[0].file_hash
        assert edit.file_hash == IndexStore.compute_file_hash(
            project_dir / "mod.py"
        )

    def test_replanned_edits_apply_cleanly(self, indexed_project):
        project_dir, store = indexed_project({"mod.py": SOURCE})
        fix = _foo_fix()
        fix.plan(store)  # detection-time plan, deliberately discarded
        (project_dir / "mod.py").write_text("import os\n\n" + SOURCE)

        plan = fix.plan(store)
        assert isinstance(plan, FixPlan)
        tx_store = TransactionStore(project_dir)
        header = TransactionHeader(
            tx_id="fix-tx-2",
            symbol_id="mod:foo",
            old_name="foo",
            new_name="bar",
            created_at="2026-06-11T00:00:00+00:00",
            operation="fix",
        )
        tx_store.save(header, plan.edits)
        result = TransactionApplier(store, tx_store).apply("fix-tx-2")

        assert result["status"] == "applied"
        assert (
            project_dir / "mod.py"
        ).read_text() == "import os\n\ndef bar():\n    return 1\n"


class TestViolationFixField:
    def test_with_fix_is_the_attachment_idiom(self):
        violation = Violation("a.py", 3, "rule", "msg")
        fix = _foo_fix()

        attached = with_fix(violation, fix)

        assert attached.fix is fix
        assert violation.fix is None  # original untouched (frozen)
        assert isinstance(fix, Fix)  # satisfies the runtime protocol

    def test_no_fix_violations_unchanged(self):
        violation = Violation("a.py", 3, "rule", "msg")
        assert violation.fix is None
        assert str(violation) == "a.py:3: [rule] msg"
        assert violation == Violation("a.py", 3, "rule", "msg")
        assert hash(violation) == hash(Violation("a.py", 3, "rule", "msg"))

    def test_fix_excluded_from_comparison_repr_and_str(self):
        plain = Violation("a.py", 3, "rule", "msg")
        carrying = with_fix(plain, _foo_fix())

        assert carrying == plain
        assert hash(carrying) == hash(plain)
        assert str(carrying) == str(plain)
        assert repr(carrying) == repr(plain)  # repr=False hides the field

    def test_sort_order_regression_with_mixed_fixes(self):
        plain = [
            Violation("b.py", 2, "rule", "m"),
            Violation("a.py", 5, "z-rule", "m"),
            Violation("a.py", 5, "a-rule", "m"),
            Violation("a.py", 1, "rule", "zz"),
            Violation("a.py", 1, "rule", "aa"),
        ]
        mixed = [
            with_fix(v, _foo_fix()) if i % 2 == 0 else v
            for i, v in enumerate(plain)
        ]

        expected = sorted(
            (v.file_path, v.line, v.rule, v.message) for v in plain
        )
        got = [
            (v.file_path, v.line, v.rule, v.message) for v in sorted(mixed)
        ]
        assert got == expected

    def test_fix_field_contract(self):
        # Guards the dataclass-field settings the sort/print semantics rely
        # on: optional, excluded from comparison and repr.
        fix_field = {f.name: f for f in dataclasses.fields(Violation)}["fix"]
        assert fix_field.default is None
        assert fix_field.compare is False
        assert fix_field.repr is False
