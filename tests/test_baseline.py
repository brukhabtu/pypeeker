"""Tests for the check baseline/ratchet engine (TASK-98).

Unit tests exercise identity, counting semantics, and the write/load/delta API
directly; CLI tests drive the full ``check --update-baseline`` /
``check --baseline`` ratchet workflow against a tmp project with the
require-docstrings rule enabled.

The identity these unit tests pin is the flip's, not the frozen engine's:
:func:`~pypeeker.storage.baseline_identity` keys a row on ``rule::anchor_id``,
where the anchor is the model row the finding was rendered from. The frozen
scheme was ``rule::file_path::normalized_message``, which needed a regex to
strip volatile ``(line N)`` fragments out of the message before it could be a
stable key. Keying on the anchor makes that normalization unnecessary rather
than merely different — the message is not part of the key at all — so the two
schemes disagree on exactly one thing, tested below: two rows about the *same*
anchor now share an identity however differently they are worded.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from click.testing import CliRunner

from pypeeker.cli import main
from pypeeker.dsl import Finding
from pypeeker.models import Confidence
from pypeeker.storage import (
    baseline_identity,
    baseline_path,
    delta,
    load_baseline,
    write_baseline,
)


def _f(
    file_path: str,
    line: int,
    rule: str,
    message: str,
    *,
    anchor_id: str | None = None,
) -> Finding:
    """One reported row, keyed by ``anchor_id`` (defaulting to its path).

    The default anchor is the file path alone, which is what lets these tests
    keep saying "the same finding, drifted to another line" without inventing
    symbol ids; where a case needs two *distinct* rows in one file it passes
    ``anchor_id`` explicitly.
    """
    return Finding(
        rule=rule,
        path=file_path,
        line=line,
        message=message,
        confidence=Confidence.DECLARED,
        anchor_id=file_path if anchor_id is None else anchor_id,
    )


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_identity_is_line_independent():
    a = _f("src/m.py", 3, "require-docstrings", "public function 'foo' has no docstring")
    b = _f("src/m.py", 30, "require-docstrings", "public function 'foo' has no docstring")
    assert baseline_identity(a) == baseline_identity(b)


def test_identity_ignores_the_message_entirely():
    """The one behaviour change from the frozen ``rule::file::message`` key.

    The frozen identity normalized volatile ``(line N)`` fragments out of the
    message so that a drifting impurity finding stayed baselined. Keying on the
    anchor subsumes that: the message is not in the key, so two rows about one
    anchor collide no matter how far apart their wording drifts — including the
    volatile-fragment case the frozen regex existed to handle.
    """
    a = _f("src/m.py", 3, "no-impure-functions", "impure: GlobalWrite 'x' (line 5)")
    b = _f("src/m.py", 9, "no-impure-functions", "impure: GlobalWrite 'x' (line 50)")
    reworded = _f("src/m.py", 9, "no-impure-functions", "a completely different message")
    assert baseline_identity(a) == baseline_identity(b) == baseline_identity(reworded)


def test_identity_distinguishes_rule_and_anchor():
    base = _f("src/m.py", 1, "require-docstrings", "public function 'foo' has no docstring")
    other_rule = _f("src/m.py", 1, "no-unresolved-refs", base.message)
    other_file = _f("src/n.py", 1, base.rule, base.message)
    other_anchor = _f("src/m.py", 1, base.rule, base.message, anchor_id="src/m.py:bar")
    identities = {
        baseline_identity(f) for f in (base, other_rule, other_file, other_anchor)
    }
    assert len(identities) == 4


# ---------------------------------------------------------------------------
# Storage round-trip
# ---------------------------------------------------------------------------


def test_write_load_round_trip(tmp_path):
    path = tmp_path / ".pypeeker" / "check-baseline.json"
    findings = [
        _f("src/m.py", 1, "require-docstrings", "public function 'foo' has no docstring"),
        _f("src/m.py", 9, "require-docstrings", "public function 'foo' has no docstring"),
        _f("src/n.py", 2, "no-unresolved-refs", "unresolved reference: 'x'"),
    ]
    counts = write_baseline(path, findings)
    assert load_baseline(path) == counts
    assert sum(counts.values()) == 3
    assert counts[baseline_identity(findings[0])] == 2


def test_baseline_file_is_sorted_stable_and_namespaced(tmp_path):
    path = tmp_path / "check-baseline.json"
    findings = [
        _f("src/z.py", 1, "rule", "zzz"),
        _f("src/a.py", 1, "rule", "aaa"),
    ]
    write_baseline(path, findings)
    data = json.loads(path.read_text())
    # Namespaced for future ratchets (TASK-99 born-private facts join here).
    assert set(data) == {"violations"}
    keys = list(data["violations"])
    assert keys == sorted(keys)
    # Stable output: rewriting identical findings is byte-identical.
    first = path.read_text()
    write_baseline(path, list(reversed(findings)))
    assert path.read_text() == first


def test_write_baseline_preserves_other_namespaces(tmp_path):
    path = tmp_path / "check-baseline.json"
    path.write_text(json.dumps({"born_private": {"m:f": True}}))
    write_baseline(path, [_f("src/m.py", 1, "rule", "msg")])
    data = json.loads(path.read_text())
    assert data["born_private"] == {"m:f": True}
    assert data["violations"] == {"rule::src/m.py": 1}


def test_load_baseline_missing_file_is_empty(tmp_path):
    assert load_baseline(tmp_path / "nope.json") == {}


def test_baseline_path_location(tmp_path):
    assert baseline_path(tmp_path) == tmp_path / ".pypeeker" / "check-baseline.json"


# ---------------------------------------------------------------------------
# Delta semantics
# ---------------------------------------------------------------------------


def test_line_drift_stays_baselined(tmp_path):
    path = tmp_path / "b.json"
    original = _f("src/m.py", 3, "require-docstrings", "public function 'foo' has no docstring")
    baseline = write_baseline(path, [original])
    drifted = _f("src/m.py", 42, original.rule, original.message)
    new, fixed = delta([drifted], baseline)
    assert new == []
    assert fixed == []


def test_new_finding_detected():
    old = _f("src/m.py", 1, "require-docstrings", "public function 'foo' has no docstring")
    baseline = {baseline_identity(old): 1}
    fresh = _f(
        "src/m.py",
        9,
        "require-docstrings",
        "public function 'bar' has no docstring",
        anchor_id="src/m.py:bar",
    )
    new, fixed = delta([old, fresh], baseline)
    assert new == [fresh]
    assert fixed == []


def test_duplicate_counts_within_budget_are_clean():
    msg = "public function 'foo' has no docstring"
    baseline = {baseline_identity(_f("src/m.py", 0, "r", msg)): 2}
    current = [_f("src/m.py", 5, "r", msg), _f("src/m.py", 80, "r", msg)]
    new, fixed = delta(current, baseline)
    assert new == []
    assert fixed == []


def test_duplicate_over_count_surplus_picks_last_occurrences():
    msg = "public function 'foo' has no docstring"
    baseline = {baseline_identity(_f("src/m.py", 0, "r", msg)): 1}
    current = [
        _f("src/m.py", 5, "r", msg),
        _f("src/m.py", 12, "r", msg),
        _f("src/m.py", 80, "r", msg),
    ]
    new, _ = delta(current, baseline)
    # Surplus of 2: deterministically the LAST occurrences in the caller's
    # order — which the run service guarantees is `(path, line, rule, message)`.
    assert new == [_f("src/m.py", 12, "r", msg), _f("src/m.py", 80, "r", msg)]


def test_unsorted_findings_attribute_the_surplus_to_different_rows():
    """The caller obligation, made executable: delta trusts the order it is given.

    ``storage.delta`` deliberately does not sort — a :class:`~pypeeker.dsl.Finding`
    is not orderable and a reported row is not required to be — so "the surplus
    is the last occurrences" means "last in the order you gave". Nothing in
    ``Finding`` can enforce that; the only thing standing between a caller and a
    silently different ``new`` set is
    :func:`~pypeeker.app.check_run.run_check`'s explicit sort. This test fails
    the day ``delta`` starts sorting for itself, which would be a fine outcome
    and should be a deliberate one.
    """
    msg = "same"
    rows = [
        _f("src/a.py", 10, "prefer-tuple", msg),
        _f("src/a.py", 20, "prefer-tuple", msg),
        _f("src/a.py", 30, "prefer-tuple", msg),
    ]
    baseline = {baseline_identity(rows[0]): 1}

    sorted_new, _ = delta(rows, baseline)
    unsorted_new, _ = delta(list(reversed(rows)), baseline)

    assert [f.line for f in sorted_new] == [20, 30]
    assert [f.line for f in unsorted_new] == [20, 10]


def test_fixed_identities_reported_and_shrink_on_update(tmp_path):
    path = tmp_path / "b.json"
    kept = _f("src/m.py", 1, "r", "kept")
    gone = _f("src/m.py", 2, "r", "gone", anchor_id="src/m.py:gone")
    baseline = write_baseline(path, [kept, gone])

    new, fixed = delta([kept], baseline)
    assert new == []
    assert fixed == [baseline_identity(gone)]

    # --update-baseline path: rewriting with current findings shrinks it.
    shrunk = write_baseline(path, [kept])
    assert baseline_identity(gone) not in shrunk
    assert load_baseline(path) == {baseline_identity(kept): 1}


def test_reduced_duplicate_count_is_fixed():
    msg = "dup"
    identity = baseline_identity(_f("src/m.py", 0, "r", msg))
    baseline = {identity: 3}
    new, fixed = delta([_f("src/m.py", 7, "r", msg)], baseline)
    assert new == []
    assert fixed == [identity]


def test_retiering_a_finding_does_not_churn_the_baseline():
    """A row whose confidence tier moves keeps its baseline identity.

    ``Finding.confidence`` joins value equality (unlike the frozen
    ``Violation``'s ``compare=False`` tier), so this is worth pinning
    separately: the *identity* is ``(rule, anchor_id)`` and neither field is
    the tier, so re-tiering a rule cannot silently re-report every baselined
    row as new.
    """
    declared = _f("src/m.py", 1, "r", "msg")
    heuristic = Finding(
        rule=declared.rule,
        path=declared.path,
        line=declared.line,
        message=declared.message,
        confidence=Confidence.HEURISTIC,
        anchor_id=declared.anchor_id,
    )
    assert declared != heuristic
    assert baseline_identity(declared) == baseline_identity(heuristic)

    new, fixed = delta([heuristic], {baseline_identity(declared): 1})
    assert new == []
    assert fixed == []


# ---------------------------------------------------------------------------
# CLI workflow
# ---------------------------------------------------------------------------

UNDOCUMENTED = "def foo():\n    return 1\n"


def _ratchet_project(tmp_path: Path, runner: CliRunner) -> Path:
    """tmp project with require-docstrings enabled and one violating file."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "test"\n'
        "[tool.pypeeker]\n"
        'src = ["src"]\n'
        'rules = ["require-docstrings"]\n'
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.py").write_text(UNDOCUMENTED)
    os.chdir(tmp_path)
    result = runner.invoke(main, ["index", str(src / "m.py")], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return tmp_path


def test_cli_baseline_then_clean_run_exits_zero(tmp_path):
    runner = CliRunner()
    project = _ratchet_project(tmp_path, runner)

    # Plain check fails on the legacy violation.
    plain = runner.invoke(main, ["check"], catch_exceptions=False)
    assert plain.exit_code == 1
    assert "'foo' has no docstring" in plain.output

    # Record the baseline.
    update = runner.invoke(main, ["check", "--update-baseline"], catch_exceptions=False)
    assert update.exit_code == 0, update.output
    assert "1 violation(s)" in update.output
    assert (project / ".pypeeker" / "check-baseline.json").exists()

    # Ratchet run is clean and summarizes.
    ratchet = runner.invoke(main, ["check", "--baseline"], catch_exceptions=False)
    assert ratchet.exit_code == 0, ratchet.output
    assert "1 baselined, 0 new, 0 fixed" in ratchet.output
    assert "no docstring" not in ratchet.output  # baselined violations not printed

    # Plain check (no flags) still fails: default behavior unchanged.
    plain_again = runner.invoke(main, ["check"], catch_exceptions=False)
    assert plain_again.exit_code == 1


def test_cli_baseline_survives_line_drift(tmp_path):
    runner = CliRunner()
    project = _ratchet_project(tmp_path, runner)
    runner.invoke(main, ["check", "--update-baseline"], catch_exceptions=False)

    # Unrelated edit shifts the violation's line; check auto-refreshes the index.
    (project / "src" / "m.py").write_text("# header\n# comment\n\n" + UNDOCUMENTED)

    result = runner.invoke(main, ["check", "--baseline"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "1 baselined, 0 new, 0 fixed" in result.output


REF_CONSUMER = (
    '"""Consumer."""\n\n\n'
    'def use():\n    """Use."""\n    return add(1, 2)\n\n\n'
    'def use_again():\n    """Again."""\n    return add(3, 4)\n'
)


def _reference_ratchet_project(tmp_path: Path, runner: CliRunner) -> Path:
    """tmp project whose only rule anchors findings on REFERENCES, not symbols."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "test"\n'
        "[tool.pypeeker]\n"
        'src = ["pkg"]\n'
        'rules = ["no-unresolved-refs"]\n'
    )
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "consumer.py").write_text(REF_CONSUMER)
    os.chdir(tmp_path)
    result = runner.invoke(main, ["index", str(pkg)], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return tmp_path


def test_cli_baseline_of_a_reference_anchored_rule_survives_line_drift(tmp_path):
    """A reference anchor names a use site by line; its baseline key must not.

    Regression: keying the baseline on the raw reference anchor id
    (``<symbol-id>@<file>:<line>:<column>``) made every ``--baseline`` run
    after an unrelated edit report false new *and* false fixed violations.
    """
    runner = CliRunner()
    project = _reference_ratchet_project(tmp_path, runner)
    update = runner.invoke(main, ["check", "--update-baseline"], catch_exceptions=False)
    assert update.exit_code == 0, update.output
    # Two occurrences of one name in one file share an identity and are COUNTED.
    recorded = json.loads(
        (project / ".pypeeker" / "check-baseline.json").read_text()
    )["violations"]
    assert recorded == {"no-unresolved-refs::add@pkg/consumer.py": 2}

    # An unrelated edit above them shifts every line.
    (project / "pkg" / "consumer.py").write_text("# a\n# b\n" + REF_CONSUMER)
    drifted = runner.invoke(main, ["check", "--baseline"], catch_exceptions=False)
    assert drifted.exit_code == 0, drifted.output
    assert "2 baselined, 0 new, 0 fixed" in drifted.output

    # Counting still catches a genuinely new occurrence of the same identity.
    (project / "pkg" / "consumer.py").write_text(
        "# a\n# b\n"
        + REF_CONSUMER
        + '\n\ndef third():\n    """Third."""\n    return add(5, 6)\n'
    )
    grown = runner.invoke(main, ["check", "--baseline"], catch_exceptions=False)
    assert grown.exit_code == 1
    assert "2 baselined, 1 new, 0 fixed" in grown.output


def test_cli_baseline_flags_only_new_violation(tmp_path):
    runner = CliRunner()
    project = _ratchet_project(tmp_path, runner)
    runner.invoke(main, ["check", "--update-baseline"], catch_exceptions=False)

    # A genuinely new violation in the same (re-indexed) file.
    (project / "src" / "m.py").write_text(
        UNDOCUMENTED + "\ndef bar():\n    return 2\n"
    )

    result = runner.invoke(main, ["check", "--baseline"], catch_exceptions=False)
    assert result.exit_code == 1
    assert "'bar' has no docstring" in result.output
    assert "'foo'" not in result.output  # only the NEW violation is listed
    assert "1 baselined, 1 new, 0 fixed" in result.output

    # Updating the baseline absorbs it; the next ratchet run is clean.
    update = runner.invoke(main, ["check", "--update-baseline"], catch_exceptions=False)
    assert update.exit_code == 0
    assert "2 violation(s)" in update.output
    clean = runner.invoke(main, ["check", "--baseline"], catch_exceptions=False)
    assert clean.exit_code == 0
    assert "2 baselined, 0 new, 0 fixed" in clean.output


def test_cli_update_baseline_shrinks_after_fix(tmp_path):
    runner = CliRunner()
    project = _ratchet_project(tmp_path, runner)
    runner.invoke(main, ["check", "--update-baseline"], catch_exceptions=False)

    # Fix the violation; the ratchet reports it fixed.
    (project / "src" / "m.py").write_text('def foo():\n    """Doc."""\n    return 1\n')
    fixed_run = runner.invoke(main, ["check", "--baseline"], catch_exceptions=False)
    assert fixed_run.exit_code == 0
    assert "1 baselined, 0 new, 1 fixed" in fixed_run.output

    # Updating shrinks the stored baseline to empty.
    update = runner.invoke(main, ["check", "--update-baseline"], catch_exceptions=False)
    assert update.exit_code == 0
    assert "0 violation(s)" in update.output
    data = json.loads(
        (project / ".pypeeker" / "check-baseline.json").read_text()
    )
    assert data["violations"] == {}
    empty = runner.invoke(main, ["check", "--baseline"], catch_exceptions=False)
    assert empty.exit_code == 0
    assert "0 baselined, 0 new, 0 fixed" in empty.output


def test_cli_baseline_without_file_treats_everything_as_new(tmp_path):
    runner = CliRunner()
    _ratchet_project(tmp_path, runner)
    result = runner.invoke(main, ["check", "--baseline"], catch_exceptions=False)
    assert result.exit_code == 1
    assert "'foo' has no docstring" in result.output
    assert "0 baselined, 1 new, 0 fixed" in result.output


def test_cli_baseline_and_update_baseline_conflict(tmp_path):
    runner = CliRunner()
    _ratchet_project(tmp_path, runner)
    result = runner.invoke(main, ["check", "--baseline", "--update-baseline"])
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output
