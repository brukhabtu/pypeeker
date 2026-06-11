"""Tests for the check baseline/ratchet engine (TASK-98).

Unit tests exercise identity normalization, counting semantics, and the
write/load/delta API directly; CLI tests drive the full
``check --update-baseline`` / ``check --baseline`` ratchet workflow against a
tmp project with the require-docstrings rule enabled.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from click.testing import CliRunner

from pypeeker.check.baseline import (
    baseline_path,
    delta,
    load_baseline,
    _violation_identity as violation_identity,
    write_baseline,
)
from pypeeker.check.models import Violation
from pypeeker.cli import main


def _v(file_path: str, line: int, rule: str, message: str) -> Violation:
    return Violation(file_path=file_path, line=line, rule=rule, message=message)


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_identity_is_line_independent():
    a = _v("src/m.py", 3, "require-docstrings", "public function 'foo' has no docstring")
    b = _v("src/m.py", 30, "require-docstrings", "public function 'foo' has no docstring")
    assert violation_identity(a) == violation_identity(b)


def test_identity_strips_volatile_line_fragments():
    a = _v("src/m.py", 3, "no-impure-functions", "impure: GlobalWrite 'x' (line 5)")
    b = _v("src/m.py", 9, "no-impure-functions", "impure: GlobalWrite 'x' (line 50)")
    assert violation_identity(a) == violation_identity(b)


def test_identity_distinguishes_rule_file_and_message():
    base = _v("src/m.py", 1, "require-docstrings", "public function 'foo' has no docstring")
    other_rule = _v("src/m.py", 1, "no-unresolved-refs", base.message)
    other_file = _v("src/n.py", 1, base.rule, base.message)
    other_msg = _v("src/m.py", 1, base.rule, "public function 'bar' has no docstring")
    identities = {
        violation_identity(v) for v in (base, other_rule, other_file, other_msg)
    }
    assert len(identities) == 4


# ---------------------------------------------------------------------------
# Storage round-trip
# ---------------------------------------------------------------------------


def test_write_load_round_trip(tmp_path):
    path = tmp_path / ".semantic-tool" / "check-baseline.json"
    violations = [
        _v("src/m.py", 1, "require-docstrings", "public function 'foo' has no docstring"),
        _v("src/m.py", 9, "require-docstrings", "public function 'foo' has no docstring"),
        _v("src/n.py", 2, "no-unresolved-refs", "unresolved reference: 'x'"),
    ]
    counts = write_baseline(path, violations)
    assert load_baseline(path) == counts
    assert sum(counts.values()) == 3
    assert counts[violation_identity(violations[0])] == 2


def test_baseline_file_is_sorted_stable_and_namespaced(tmp_path):
    path = tmp_path / "check-baseline.json"
    violations = [
        _v("src/z.py", 1, "rule", "zzz"),
        _v("src/a.py", 1, "rule", "aaa"),
    ]
    write_baseline(path, violations)
    data = json.loads(path.read_text())
    # Namespaced for future ratchets (TASK-99 born-private facts join here).
    assert set(data) == {"violations"}
    keys = list(data["violations"])
    assert keys == sorted(keys)
    # Stable output: rewriting identical violations is byte-identical.
    first = path.read_text()
    write_baseline(path, list(reversed(violations)))
    assert path.read_text() == first


def test_write_baseline_preserves_other_namespaces(tmp_path):
    path = tmp_path / "check-baseline.json"
    path.write_text(json.dumps({"born_private": {"m:f": True}}))
    write_baseline(path, [_v("src/m.py", 1, "rule", "msg")])
    data = json.loads(path.read_text())
    assert data["born_private"] == {"m:f": True}
    assert data["violations"] == {"rule::src/m.py::msg": 1}


def test_load_baseline_missing_file_is_empty(tmp_path):
    assert load_baseline(tmp_path / "nope.json") == {}


def test_baseline_path_location(tmp_path):
    assert baseline_path(tmp_path) == tmp_path / ".semantic-tool" / "check-baseline.json"


# ---------------------------------------------------------------------------
# Delta semantics
# ---------------------------------------------------------------------------


def test_line_drift_stays_baselined(tmp_path):
    path = tmp_path / "b.json"
    original = _v("src/m.py", 3, "require-docstrings", "public function 'foo' has no docstring")
    baseline = write_baseline(path, [original])
    drifted = _v("src/m.py", 42, original.rule, original.message)
    new, fixed = delta([drifted], baseline)
    assert new == []
    assert fixed == []


def test_new_violation_detected():
    old = _v("src/m.py", 1, "require-docstrings", "public function 'foo' has no docstring")
    baseline = {violation_identity(old): 1}
    fresh = _v("src/m.py", 9, "require-docstrings", "public function 'bar' has no docstring")
    new, fixed = delta([old, fresh], baseline)
    assert new == [fresh]
    assert fixed == []


def test_duplicate_counts_within_budget_are_clean():
    msg = "public function 'foo' has no docstring"
    baseline = {violation_identity(_v("src/m.py", 0, "r", msg)): 2}
    current = [_v("src/m.py", 5, "r", msg), _v("src/m.py", 80, "r", msg)]
    new, fixed = delta(current, baseline)
    assert new == []
    assert fixed == []


def test_duplicate_over_count_surplus_picks_last_occurrences():
    msg = "public function 'foo' has no docstring"
    baseline = {violation_identity(_v("src/m.py", 0, "r", msg)): 1}
    current = [
        _v("src/m.py", 5, "r", msg),
        _v("src/m.py", 80, "r", msg),
        _v("src/m.py", 12, "r", msg),
    ]
    new, _ = delta(current, baseline)
    # Surplus of 2: deterministically the LAST occurrences in line order.
    assert new == [_v("src/m.py", 12, "r", msg), _v("src/m.py", 80, "r", msg)]


def test_fixed_identities_reported_and_shrink_on_update(tmp_path):
    path = tmp_path / "b.json"
    kept = _v("src/m.py", 1, "r", "kept")
    gone = _v("src/m.py", 2, "r", "gone")
    baseline = write_baseline(path, [kept, gone])

    new, fixed = delta([kept], baseline)
    assert new == []
    assert fixed == [violation_identity(gone)]

    # --update-baseline path: rewriting with current violations shrinks it.
    shrunk = write_baseline(path, [kept])
    assert violation_identity(gone) not in shrunk
    assert load_baseline(path) == {violation_identity(kept): 1}


def test_reduced_duplicate_count_is_fixed():
    msg = "dup"
    identity = violation_identity(_v("src/m.py", 0, "r", msg))
    baseline = {identity: 3}
    new, fixed = delta([_v("src/m.py", 7, "r", msg)], baseline)
    assert new == []
    assert fixed == [identity]


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
    assert (project / ".semantic-tool" / "check-baseline.json").exists()

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
        (project / ".semantic-tool" / "check-baseline.json").read_text()
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
