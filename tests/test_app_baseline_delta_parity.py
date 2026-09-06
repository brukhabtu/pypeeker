"""TEMPORARY: grade ``storage.baseline.delta`` against the frozen ``check.baseline.delta``.

The two deltas differ in one respect that no other test can see. The frozen
one sorts its input (``sorted(violations)``) *and* its output (``new.sort()``);
the new one does neither, because a :class:`~pypeeker.dsl.Finding` is not
orderable and a reported row is not required to be. What replaces the internal
sort is a **caller obligation** — ``pypeeker.app.check_run2.run_dsl_check``
sorts by ``(path, line, rule, message)`` before anything reaches the baseline —
and an obligation discharged by a caller is exactly the kind of contract that
rots silently.

So this file builds parallel ``Violation`` / ``Finding`` lists whose two
identity functions induce the *same* partition (``rule::anchor_id`` collides
exactly where ``rule::file_path::normalized_message`` does, by setting
``anchor_id`` to ``f"{path}::{message}"``) and asserts the two functions agree
element for element on every case where the attribution could drift: scrambled
input, a surplus larger than the baselined count, a surplus of zero, an
identity absent from the baseline, and a baselined identity that vanished.

It also pins the *failure*: the last test shows the new delta genuinely
attributing the surplus to different rows when its input is unsorted, so the
obligation is documented in executable form rather than only in a docstring.

This file exists only while both ``check/baseline.py`` and
``storage/baseline.py`` do. Phase B deletes the frozen half, and this file goes
with it, with a ledger line — the assertions it makes about the new delta alone
are already covered by ``tests/test_app_check_run2.py``.
"""

from __future__ import annotations

from pypeeker.check.baseline import delta as frozen_delta
from pypeeker.check.models import Violation
from pypeeker.dsl import Finding
from pypeeker.models import Confidence
from pypeeker.storage import delta as new_delta


def _row(rule: str, path: str, line: int, message: str) -> tuple[Violation, Finding]:
    """One reported row in both engines' shapes, sharing a baseline identity.

    ``anchor_id`` is ``"<path>::<message>"`` so that ``rule::anchor_id`` is the
    same string the frozen ``rule::file_path::normalized_message`` produces —
    the messages here carry no ``(line N)`` fragment, so normalization is the
    identity. That is what makes the two partitions comparable at all.
    """
    return (
        Violation(file_path=path, line=line, rule=rule, message=message),
        Finding(
            rule=rule,
            path=path,
            line=line,
            message=message,
            confidence=Confidence.DECLARED,
            anchor_id=f"{path}::{message}",
        ),
    )


def _sorted(rows: list[tuple[Violation, Finding]]) -> list[tuple[Violation, Finding]]:
    """The rows in the order ``run_dsl_check`` produces: (path, line, rule, message)."""
    return sorted(rows, key=lambda row: (row[1].path, row[1].line, row[1].rule, row[1].message))


def _assert_agree(
    violations: list[Violation],
    findings: list[Finding],
    baseline: dict[str, int],
) -> tuple[list[Finding], list[str]]:
    """Both deltas name the same rows new and the same identities fixed."""
    old_new, old_fixed = frozen_delta(violations, baseline)
    fresh_new, fresh_fixed = new_delta(findings, baseline)
    assert [str(v) for v in old_new] == [str(f) for f in fresh_new]
    assert old_fixed == fresh_fixed
    return fresh_new, fresh_fixed


# ── (1) scrambled input ─────────────────────────────────────────────────────


def test_a_scrambled_input_reaches_the_frozen_answer_once_the_caller_sorts():
    """The frozen delta sorts for itself; the new one is handed sorted rows.

    Both sides see the same *rows*; only the order they arrive in differs. If
    the caller's sort is the right one, the answers are identical — which is
    the whole claim ``run_dsl_check``'s sort rests on.
    """
    rows = [
        _row("prefer-tuple", "src/b.py", 30, "third"),
        _row("prefer-tuple", "src/a.py", 10, "first"),
        _row("require-docstrings", "src/a.py", 10, "second"),
        _row("prefer-tuple", "src/a.py", 99, "first"),
    ]
    scrambled = [violation for violation, _ in rows]
    ordered = [finding for _, finding in _sorted(rows)]

    new, fixed = _assert_agree(scrambled, ordered, {"prefer-tuple::src/a.py::first": 1})

    # Two occurrences of one identity, one baselined: the LATER one is new.
    assert [(f.path, f.line) for f in new] == [
        ("src/a.py", 10),
        ("src/a.py", 99),
        ("src/b.py", 30),
    ]
    assert fixed == []


# ── (2) surplus > baselined count ───────────────────────────────────────────


def test_a_surplus_larger_than_the_baselined_count_takes_the_last_occurrences():
    """Three occurrences, one baselined: the two later rows are the new ones."""
    rows = _sorted([
        _row("prefer-tuple", "src/a.py", 10, "same"),
        _row("prefer-tuple", "src/a.py", 20, "same"),
        _row("prefer-tuple", "src/a.py", 30, "same"),
    ])

    new, fixed = _assert_agree(
        [violation for violation, _ in rows],
        [finding for _, finding in rows],
        {"prefer-tuple::src/a.py::same": 1},
    )

    assert [f.line for f in new] == [20, 30]
    assert fixed == []


# ── (3) surplus == 0 ────────────────────────────────────────────────────────


def test_an_exactly_baselined_identity_reports_nothing():
    """Occurrences equal to the baselined count: no new rows, nothing fixed."""
    rows = _sorted([
        _row("prefer-tuple", "src/a.py", 10, "same"),
        _row("prefer-tuple", "src/a.py", 20, "same"),
    ])

    new, fixed = _assert_agree(
        [violation for violation, _ in rows],
        [finding for _, finding in rows],
        {"prefer-tuple::src/a.py::same": 2},
    )

    assert new == []
    assert fixed == []


# ── (4) an identity absent from the baseline ────────────────────────────────


def test_an_identity_the_baseline_never_saw_is_new_in_every_occurrence():
    """A baseline naming *other* identities does not shelter this one."""
    rows = _sorted([
        _row("prefer-tuple", "src/a.py", 10, "brand new"),
        _row("prefer-tuple", "src/a.py", 20, "brand new"),
    ])

    new, fixed = _assert_agree(
        [violation for violation, _ in rows],
        [finding for _, finding in rows],
        {"prefer-tuple::src/other.py::unrelated": 5},
    )

    assert [f.line for f in new] == [10, 20]
    assert fixed == ["prefer-tuple::src/other.py::unrelated"]


# ── (5) a baselined identity that vanished ──────────────────────────────────


def test_a_vanished_identity_is_reported_fixed_by_both():
    """Zero current occurrences of a baselined identity: fixed, on both sides."""
    rows = _sorted([_row("prefer-tuple", "src/a.py", 10, "still here")])

    new, fixed = _assert_agree(
        [violation for violation, _ in rows],
        [finding for _, finding in rows],
        {
            "prefer-tuple::src/a.py::still here": 1,
            "prefer-tuple::src/a.py::gone": 2,
            "require-docstrings::src/b.py::also gone": 1,
        },
    )

    assert new == []
    assert fixed == [
        "prefer-tuple::src/a.py::gone",
        "require-docstrings::src/b.py::also gone",
    ]


def test_a_partially_fixed_identity_counts_as_fixed_on_both_sides():
    """Two baselined, one left: the count dropped, so the identity is fixed."""
    rows = _sorted([_row("prefer-tuple", "src/a.py", 10, "same")])

    new, fixed = _assert_agree(
        [violation for violation, _ in rows],
        [finding for _, finding in rows],
        {"prefer-tuple::src/a.py::same": 2},
    )

    assert new == []
    assert fixed == ["prefer-tuple::src/a.py::same"]


# ── the obligation itself ───────────────────────────────────────────────────


def test_unsorted_findings_attribute_the_surplus_to_different_rows():
    """The hazard, made executable: the new delta trusts the order it is given.

    Nothing in ``Finding`` can enforce this — it is not orderable — so the only
    thing standing between a caller and a silently different ``new`` set is
    ``run_dsl_check``'s explicit sort. This test fails the day the new delta
    starts sorting for itself, which would be a fine outcome and should be a
    deliberate one.
    """
    rows = [
        _row("prefer-tuple", "src/a.py", 10, "same"),
        _row("prefer-tuple", "src/a.py", 20, "same"),
        _row("prefer-tuple", "src/a.py", 30, "same"),
    ]
    baseline = {"prefer-tuple::src/a.py::same": 1}
    reversed_rows = list(reversed(rows))

    frozen_new, _ = frozen_delta([violation for violation, _ in reversed_rows], baseline)
    unsorted_new, _ = new_delta([finding for _, finding in reversed_rows], baseline)

    assert [v.line for v in frozen_new] == [20, 30], "the frozen delta re-sorts"
    assert [f.line for f in unsorted_new] == [20, 10], "the new delta does not"
