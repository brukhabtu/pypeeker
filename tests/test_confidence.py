"""Tests for confidence tiers on check violations (TASK-83).

Covers the structured ``Violation.confidence`` field (default, eq/order/hash
regression, the ``__str__`` tier marker), the migrated dynamic-access
labeling (rules emit ``confidence=HEURISTIC`` instead of the old TASK-95
message suffix), the purity-derived HEURISTIC labeling, and the CLI behavior:
default runs omit low-confidence violations with a summary note, ``--strict``
includes them, and baseline flows always operate on the full violation set.
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from pypeeker.check.context import CheckContext
from pypeeker.check.models import Violation
from pypeeker.check.rules import no_impure_functions, unused_public_symbol
from pypeeker.cli import main
from pypeeker.models.capabilities import Confidence


def _v(message: str = "m", **kwargs) -> Violation:
    return Violation(file_path="a.py", line=1, rule="r", message=message, **kwargs)


# ── field semantics ─────────────────────────────────────────────────────────


class TestConfidenceField:
    def test_default_is_declared(self):
        assert _v().confidence is Confidence.DECLARED

    def test_reuses_capabilities_enum(self):
        # No parallel enum: the field accepts the shared Confidence values.
        for tier in Confidence:
            assert _v(confidence=tier).confidence is tier

    def test_equality_ignores_confidence(self):
        assert _v(confidence=Confidence.HEURISTIC) == _v()

    def test_hash_ignores_confidence(self):
        assert hash(_v(confidence=Confidence.UNKNOWN)) == hash(_v())

    def test_ordering_ignores_confidence(self):
        # Sorting mixed-tier violations stays deterministic and is driven by
        # (file_path, line, rule, message) exactly as before.
        first = Violation("a.py", 1, "r", "m", confidence=Confidence.UNKNOWN)
        second = Violation("b.py", 1, "r", "m", confidence=Confidence.DECLARED)
        assert sorted([second, first]) == [first, second]
        assert not first > second

    def test_replace_sets_tier(self):
        replaced = dataclasses.replace(_v(), confidence=Confidence.HEURISTIC)
        assert replaced.confidence is Confidence.HEURISTIC
        assert replaced == _v()  # identity-relevant fields untouched


class TestStrMarker:
    def test_declared_output_unchanged(self):
        assert str(_v()) == "a.py:1: [r] m"

    @pytest.mark.parametrize(
        "tier", [Confidence.INFERRED, Confidence.HEURISTIC, Confidence.UNKNOWN]
    )
    def test_non_declared_gets_trailing_marker(self, tier):
        assert str(_v(confidence=tier)) == f"a.py:1: [r] m [{tier.value}]"


# ── rule labeling ───────────────────────────────────────────────────────────


@pytest.fixture
def run_project_rule(indexed_project):
    """Index ``files`` and run a project rule, returning the violations."""

    def _run(rule, files, options=None):
        _, store = indexed_project(files)
        indexes = [
            idx
            for idx in (store.load(p) for p in store.list_indexed_files())
            if idx is not None
        ]
        return rule(CheckContext(store, indexes), options or {})

    return _run


class TestDynamicAccessLabeling:
    """The TASK-95 message suffix is superseded by the structured field."""

    def test_dynamic_module_finding_is_heuristic_without_suffix(
        self, run_project_rule
    ):
        found = run_project_rule(
            unused_public_symbol,
            {
                "pkg/lib.py": (
                    "def orphan():\n    return 1\n\n"
                    "value = getattr(object, 'x', None)\n"
                )
            },
        )
        flagged = [v for v in found if "'orphan'" in v.message]
        assert flagged
        assert all(v.confidence is Confidence.HEURISTIC for v in flagged)
        assert all("low confidence" not in v.message for v in flagged)
        assert all(str(v).endswith(" [heuristic]") for v in flagged)

    def test_static_module_finding_stays_declared(self, run_project_rule):
        found = run_project_rule(
            unused_public_symbol,
            {"pkg/lib.py": "def orphan():\n    return 1\n"},
        )
        flagged = [v for v in found if "'orphan'" in v.message]
        assert flagged
        assert all(v.confidence is Confidence.DECLARED for v in flagged)


class TestImpurityLabeling:
    def test_unknown_receiver_only_is_heuristic(self, run_project_rule):
        # get() is opaque, so .write() rests on an UNKNOWN receiver match.
        found = run_project_rule(
            no_impure_functions,
            {"mod.py": "def f(get):\n    get().write('x')\n"},
            {"include": ["mod"]},
        )
        assert found
        assert all(v.confidence is Confidence.HEURISTIC for v in found)

    def test_builtin_call_is_declared(self, run_project_rule):
        found = run_project_rule(
            no_impure_functions,
            {"mod.py": "def f():\n    print('x')\n"},
            {"include": ["mod"]},
        )
        assert found
        assert all(v.confidence is Confidence.DECLARED for v in found)

    def test_strong_observation_outranks_weak_one(self, run_project_rule):
        found = run_project_rule(
            no_impure_functions,
            {"mod.py": "def f(get):\n    get().write('x')\n    print('x')\n"},
            {"include": ["mod"]},
        )
        assert found
        assert all(v.confidence is Confidence.DECLARED for v in found)


# ── CLI: --strict and the default low-confidence filter ────────────────────

HIDDEN_NOTE = "low-confidence violation(s) hidden (use --strict to show)"

DYNAMIC_ORPHAN = (
    "def orphan():\n    return 1\n\nvalue = getattr(object, 'x', None)\n"
)


def _cli_project(
    tmp_path: Path, runner: CliRunner, rules: list[str], files: dict[str, str]
) -> Path:
    """tmp project with the given rules enabled and src files indexed."""
    rule_list = ", ".join(f'"{r}"' for r in rules)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "test"\n'
        "[tool.pypeeker]\n"
        'src = ["src"]\n'
        f"rules = [{rule_list}]\n"
    )
    src = tmp_path / "src"
    src.mkdir()
    for name, content in files.items():
        path = src / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    os.chdir(tmp_path)
    result = runner.invoke(main, ["index", "src"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return tmp_path


def test_cli_default_hides_low_confidence_with_note(tmp_path):
    runner = CliRunner()
    _cli_project(
        tmp_path, runner, ["unused-public-symbol"], {"m.py": DYNAMIC_ORPHAN}
    )
    result = runner.invoke(main, ["check"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "'orphan'" not in result.output
    assert f"1 {HIDDEN_NOTE}" in result.output


def test_cli_strict_shows_low_confidence_with_marker(tmp_path):
    runner = CliRunner()
    _cli_project(
        tmp_path, runner, ["unused-public-symbol"], {"m.py": DYNAMIC_ORPHAN}
    )
    result = runner.invoke(main, ["check", "--strict"], catch_exceptions=False)
    assert result.exit_code == 1
    assert "'orphan'" in result.output
    assert "[heuristic]" in result.output
    assert HIDDEN_NOTE not in result.output


def test_cli_declared_findings_always_show(tmp_path):
    # A certain (DECLARED) violation fails the run even while the heuristic
    # one is hidden; both appear under --strict.
    runner = CliRunner()
    _cli_project(
        tmp_path,
        runner,
        ["require-docstrings", "unused-public-symbol"],
        {"m.py": DYNAMIC_ORPHAN},
    )
    default = runner.invoke(main, ["check"], catch_exceptions=False)
    assert default.exit_code == 1
    assert "has no docstring" in default.output
    assert "no references in the project" not in default.output
    assert f"1 {HIDDEN_NOTE}" in default.output

    strict = runner.invoke(main, ["check", "--strict"], catch_exceptions=False)
    assert strict.exit_code == 1
    assert "has no docstring" in strict.output
    assert "no references in the project" in strict.output


def test_cli_certain_only_runs_print_no_note(tmp_path):
    runner = CliRunner()
    _cli_project(
        tmp_path,
        runner,
        ["require-docstrings"],
        {"m.py": "def foo():\n    return 1\n"},
    )
    result = runner.invoke(main, ["check"], catch_exceptions=False)
    assert result.exit_code == 1
    assert "has no docstring" in result.output
    assert HIDDEN_NOTE not in result.output
    # DECLARED findings carry no tier marker: default output is unchanged.
    assert "[heuristic]" not in result.output


# ── CLI: baseline flows operate on the full set regardless of --strict ─────


def test_update_baseline_records_full_set_regardless_of_strict(tmp_path):
    runner = CliRunner()
    project = _cli_project(
        tmp_path, runner, ["unused-public-symbol"], {"m.py": DYNAMIC_ORPHAN}
    )
    baseline_file = project / ".semantic-tool" / "check-baseline.json"

    default = runner.invoke(
        main, ["check", "--update-baseline"], catch_exceptions=False
    )
    assert default.exit_code == 0, default.output
    assert "1 violation(s) recorded" in default.output  # heuristic included
    default_content = baseline_file.read_text()

    baseline_file.unlink()
    strict = runner.invoke(
        main, ["check", "--strict", "--update-baseline"], catch_exceptions=False
    )
    assert strict.exit_code == 0, strict.output
    assert baseline_file.read_text() == default_content

    identities = json.loads(default_content)["violations"]
    assert any("unused-public-symbol" in key for key in identities)


def test_baseline_compares_full_set_but_filters_display(tmp_path):
    runner = CliRunner()
    project = _cli_project(
        tmp_path, runner, ["unused-public-symbol"], {"m.py": "x = 1\n"}
    )
    update = runner.invoke(
        main, ["check", "--update-baseline"], catch_exceptions=False
    )
    assert update.exit_code == 0, update.output

    # Introduce a new low-confidence violation; check auto-refreshes.
    (project / "src" / "m.py").write_text(DYNAMIC_ORPHAN)

    default = runner.invoke(main, ["check", "--baseline"], catch_exceptions=False)
    assert default.exit_code == 0, default.output
    assert "0 new" in default.output
    assert f"1 {HIDDEN_NOTE}" in default.output

    strict = runner.invoke(
        main, ["check", "--baseline", "--strict"], catch_exceptions=False
    )
    assert strict.exit_code == 1
    assert "'orphan'" in strict.output
    assert "1 new" in strict.output


def test_baselined_heuristic_violation_stays_covered_without_strict(tmp_path):
    # The baseline holds the heuristic violation's identity, so a default
    # (filtered) --baseline run neither re-fires nor reports it as fixed.
    runner = CliRunner()
    _cli_project(
        tmp_path, runner, ["unused-public-symbol"], {"m.py": DYNAMIC_ORPHAN}
    )
    runner.invoke(main, ["check", "--update-baseline"], catch_exceptions=False)

    result = runner.invoke(main, ["check", "--baseline"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "1 baselined, 0 new, 0 fixed" in result.output
