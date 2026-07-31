"""Tests for ``Violation.remedy`` and the ``with_remedy`` attachment idiom.

Since TASK-124 a rule proposes a repair by attaching an
:class:`~pypeeker.intents.Intent` to the violation it emits; the planner
behind that intent is what turns it into edits (covered end-to-end in
``tests/test_planner_ports.py`` and ``tests/test_check_fix.py``). What this
file pins is the *field contract* the rest of check depends on: attaching a
remedy must not change a violation's equality, ordering, hash, ``str`` or
``repr`` — the no-remedy regression guarantees the pre-fix engine had.

Violations are in-memory only: nothing in pypeeker serializes or persists
them (the engine returns them, the CLI prints ``str(v)``), so the remedy
object reference never needs to round-trip through JSON.
"""

from __future__ import annotations

import dataclasses

from pypeeker.check import Violation, with_remedy
from pypeeker.intents import Intent, ReplaceTextIntent


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


class TestViolationRemedyField:
    def test_with_remedy_is_the_attachment_idiom(self):
        violation = Violation("a.py", 3, "rule", "msg")
        remedy = _foo_remedy()

        attached = with_remedy(violation, remedy)

        assert attached.remedy is remedy
        assert violation.remedy is None  # original untouched (frozen)
        assert isinstance(remedy, Intent)  # it is a real schedulable intent

    def test_remedy_id_is_the_rules_stable_repair_id(self):
        # The report contract: ``check --fix`` prints this string as fix_id.
        assert _foo_remedy().intent_id == "test-rule:rename-foo"

    def test_no_remedy_violations_unchanged(self):
        violation = Violation("a.py", 3, "rule", "msg")
        assert violation.remedy is None
        assert str(violation) == "a.py:3: [rule] msg"
        assert violation == Violation("a.py", 3, "rule", "msg")
        assert hash(violation) == hash(Violation("a.py", 3, "rule", "msg"))

    def test_remedy_excluded_from_comparison_repr_and_str(self):
        plain = Violation("a.py", 3, "rule", "msg")
        carrying = with_remedy(plain, _foo_remedy())

        assert carrying == plain
        assert hash(carrying) == hash(plain)
        assert str(carrying) == str(plain)
        assert repr(carrying) == repr(plain)  # repr=False hides the field

    def test_two_violations_with_different_remedies_still_compare_equal(self):
        plain = Violation("a.py", 3, "rule", "msg")
        a = with_remedy(plain, _foo_remedy())
        b = with_remedy(plain, _foo_remedy(new_text="baz"))
        assert a == b
        assert hash(a) == hash(b)

    def test_sort_order_regression_with_mixed_remedies(self):
        plain = [
            Violation("b.py", 2, "rule", "m"),
            Violation("a.py", 5, "z-rule", "m"),
            Violation("a.py", 5, "a-rule", "m"),
            Violation("a.py", 1, "rule", "zz"),
            Violation("a.py", 1, "rule", "aa"),
        ]
        mixed = [
            with_remedy(v, _foo_remedy()) if i % 2 == 0 else v
            for i, v in enumerate(plain)
        ]

        expected = sorted(
            (v.file_path, v.line, v.rule, v.message) for v in plain
        )
        got = [
            (v.file_path, v.line, v.rule, v.message) for v in sorted(mixed)
        ]
        assert got == expected

    def test_remedy_field_contract(self):
        # Guards the dataclass-field settings the sort/print semantics rely
        # on: optional, excluded from comparison and repr.
        field = {f.name: f for f in dataclasses.fields(Violation)}["remedy"]
        assert field.default is None
        assert field.compare is False
        assert field.repr is False
