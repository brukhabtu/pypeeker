"""Tests for ``Finding.remedy`` — the field contract, not the attachment.

Since TASK-124 a rule proposes a repair by attaching an
:class:`~pypeeker.intents.Intent` to the row it emits; the planner behind that
intent is what turns it into edits (covered end-to-end in
``tests/test_planner_ports.py`` and ``tests/test_check_fix.py``). What this
file pins is the *field contract* the rest of ``check`` depends on: carrying a
remedy must not change a finding's equality, ordering, hash, ``str`` or
``repr`` — the no-remedy regression guarantee the pre-fix engine had.

The frozen engine attached a remedy after the fact, with a ``with_remedy``
helper on an already-built ``Violation``. The DSL has no such idiom: a
:class:`~pypeeker.dsl.Mutation` terminal decides the repair while the row is
being rendered, so the remedy is a construction argument. The contract the
helper existed to protect is unchanged and is what is asserted below.

Findings are in-memory only: nothing in pypeeker serializes or persists them
(the run service returns them, the CLI prints ``str(v)``), so the remedy
object reference never needs to round-trip through JSON.
"""

from __future__ import annotations

import dataclasses

from pypeeker.app.check_run import finding_order
from pypeeker.dsl import Finding
from pypeeker.intents import Intent, ReplaceTextIntent
from pypeeker.models import Confidence


def _foo_remedy(**overrides) -> ReplaceTextIntent:
    """Remedy renaming the ``foo`` token at line 0, byte column 4 to ``bar``."""
    kwargs = dict(
        intent_id="test-rule:rename-foo",
        file_path="mod.py",
        line=0,
        column=4,
        old_text="foo",
        new_text="bar",
    )
    kwargs.update(overrides)
    return ReplaceTextIntent(**kwargs)


def _finding(path="a.py", line=3, rule="rule", message="msg", **kwargs) -> Finding:
    return Finding(
        rule=rule,
        path=path,
        line=line,
        message=message,
        confidence=Confidence.DECLARED,
        **kwargs,
    )


class TestFindingRemedyField:
    def test_the_remedy_is_carried_at_construction(self):
        remedy = _foo_remedy()
        carrying = _finding(remedy=remedy)

        assert carrying.remedy is remedy
        assert _finding().remedy is None
        assert isinstance(remedy, Intent)  # it is a real schedulable intent

    def test_remedy_id_is_the_rules_stable_repair_id(self):
        # The report contract: ``check --fix`` prints this string as fix_id.
        assert _foo_remedy().intent_id == "test-rule:rename-foo"

    def test_no_remedy_findings_unchanged(self):
        finding = _finding()
        assert finding.remedy is None
        assert str(finding) == "a.py:3: [rule] msg"
        assert finding == _finding()
        assert hash(finding) == hash(_finding())

    def test_remedy_excluded_from_comparison_repr_and_str(self):
        plain = _finding()
        carrying = _finding(remedy=_foo_remedy())

        assert carrying == plain
        assert hash(carrying) == hash(plain)
        assert str(carrying) == str(plain)
        assert repr(carrying) == repr(plain)  # repr=False hides the field

    def test_two_findings_with_different_remedies_still_compare_equal(self):
        a = _finding(remedy=_foo_remedy())
        b = _finding(remedy=_foo_remedy(new_text="baz"))
        assert a == b
        assert hash(a) == hash(b)

    def test_sort_order_regression_with_mixed_remedies(self):
        plain = [
            _finding("b.py", 2, "rule", "m"),
            _finding("a.py", 5, "z-rule", "m"),
            _finding("a.py", 5, "a-rule", "m"),
            _finding("a.py", 1, "rule", "zz"),
            _finding("a.py", 1, "rule", "aa"),
        ]
        mixed = [
            dataclasses.replace(v, remedy=_foo_remedy()) if i % 2 == 0 else v
            for i, v in enumerate(plain)
        ]

        expected = sorted(map(finding_order, plain))
        got = [finding_order(v) for v in sorted(mixed, key=finding_order)]
        assert got == expected

    def test_remedy_field_contract(self):
        # Guards the dataclass-field settings the sort/print semantics rely
        # on: optional, excluded from comparison and repr.
        field = {f.name: f for f in dataclasses.fields(Finding)}["remedy"]
        assert field.default is None
        assert field.compare is False
        assert field.repr is False
