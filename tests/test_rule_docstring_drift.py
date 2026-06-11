"""Tests for the docstring-drift rule and its rename repair (TASK-93).

Covers the three style parsers (documented param sets, absent/undocumented
detection), stars normalization, style autodetection and forcing, the
require-complete gate, the allow option, the conservative fix (single
undocumented param renamed end-to-end, ambiguity declines), and the
``check --fix`` CLI path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from click.testing import CliRunner

from pypeeker.check.builtin.docstring_drift import (
    DocstringParamRenameFix,
    docstring_drift,
    parse_documented_params,
)
from pypeeker.check.fixes import DeclineReason, FixDeclined, FixPlan
from pypeeker.cli import main
from pypeeker.models.transaction import TransactionHeader
from pypeeker.refactor.applier import TransactionApplier
from pypeeker.storage import TransactionStore


def _apply_plan(project_dir, store, plan: FixPlan, tx_id: str = "fix-tx") -> None:
    """Apply a FixPlan through the standard transaction machinery."""
    tx_store = TransactionStore(project_dir)
    header = TransactionHeader(
        tx_id=tx_id,
        symbol_id="",
        old_name="",
        new_name="",
        created_at="2026-06-11T00:00:00+00:00",
        operation="check-fix",
    )
    tx_store.save(header, plan.edits)
    result = TransactionApplier(store, tx_store).apply(tx_id)
    assert result["status"] == "applied"


GOOGLE_DRIFT = (
    "def scale(value, factor):\n"
    '    """Scale a value.\n'
    "\n"
    "    Args:\n"
    "        value: The value to scale.\n"
    "        amount: The multiplier.\n"
    '    """\n'
    "    return value * factor\n"
)

NUMPY_DRIFT = (
    "def scale(value, factor):\n"
    '    """Scale a value.\n'
    "\n"
    "    Parameters\n"
    "    ----------\n"
    "    value : int\n"
    "        The value to scale.\n"
    "    amount : int\n"
    "        The multiplier.\n"
    "\n"
    "    Returns\n"
    "    -------\n"
    "    int\n"
    '    """\n'
    "    return value * factor\n"
)

SPHINX_DRIFT = (
    "def scale(value, factor):\n"
    '    """Scale a value.\n'
    "\n"
    "    :param value: The value to scale.\n"
    "    :param int amount: The multiplier.\n"
    '    """\n'
    "    return value * factor\n"
)


# ---------------------------------------------------------------------------
# Parser units
# ---------------------------------------------------------------------------


class TestParsers:
    def test_google_section_parsed(self):
        doc = (
            "Summary.\n\n"
            "    Args:\n"
            "        value: The value.\n"
            "        amount (int): The multiplier.\n"
            "            Continuation line of the description.\n"
            "    Returns:\n"
            "        Nothing."
        )
        section = parse_documented_params(doc)
        assert section is not None
        assert section.style == "google"
        assert section.names == ("value", "amount")

    def test_numpy_section_parsed_with_types(self):
        doc = (
            "Summary.\n\n"
            "    Parameters\n"
            "    ----------\n"
            "    value : int\n"
            "        The value.\n"
            "    amount\n"
            "        The multiplier.\n"
            "\n"
            "    Returns\n"
            "    -------\n"
            "    int"
        )
        section = parse_documented_params(doc)
        assert section is not None
        assert section.style == "numpy"
        assert section.names == ("value", "amount")

    def test_numpy_following_header_without_blank_line_not_a_param(self):
        doc = (
            "Summary.\n\n"
            "    Parameters\n"
            "    ----------\n"
            "    value : int\n"
            "        The value.\n"
            "    Returns\n"
            "    -------\n"
            "    int"
        )
        section = parse_documented_params(doc)
        assert section is not None
        assert section.names == ("value",)  # "Returns" is a header, not a param

    def test_sphinx_section_parsed_both_forms(self):
        doc = (
            "Summary.\n\n"
            "    :param value: The value.\n"
            "    :param int amount: The multiplier.\n"
            "    :returns: Nothing."
        )
        section = parse_documented_params(doc)
        assert section is not None
        assert section.style == "sphinx"
        assert section.names == ("value", "amount")

    def test_stars_are_normalized(self):
        doc = (
            "Summary.\n\n"
            "    Args:\n"
            "        *args: Positional.\n"
            "        **kwargs: Keyword.\n"
        )
        section = parse_documented_params(doc)
        assert section is not None
        assert section.names == ("args", "kwargs")

    def test_first_marker_wins_on_autodetect(self):
        doc = (
            ":param value: The value.\n\n"
            "Args:\n"
            "    bogus: Never read — sphinx marker appears first.\n"
        )
        section = parse_documented_params(doc)
        assert section is not None
        assert section.style == "sphinx"
        assert section.names == ("value",)

    def test_forced_style_overrides_autodetect(self):
        doc = "Summary.\n\n    Args:\n        value: The value.\n"
        assert parse_documented_params(doc, "sphinx") is None
        section = parse_documented_params(doc, "google")
        assert section is not None and section.names == ("value",)

    def test_no_section_returns_none(self):
        assert parse_documented_params("Just a summary, nothing else.") is None


# ---------------------------------------------------------------------------
# Rule behavior
# ---------------------------------------------------------------------------


class TestDocstringDriftRule:
    def test_google_ghost_param_flagged(self, bind_source):
        [violation] = docstring_drift(bind_source(GOOGLE_DRIFT, "mod.py"), {})
        assert violation.rule == "docstring-drift"
        assert "documents parameter 'amount' which does not exist" in violation.message
        assert violation.line == 1

    def test_numpy_ghost_param_flagged(self, bind_source):
        [violation] = docstring_drift(bind_source(NUMPY_DRIFT, "mod.py"), {})
        assert "'amount'" in violation.message

    def test_sphinx_ghost_param_flagged(self, bind_source):
        [violation] = docstring_drift(bind_source(SPHINX_DRIFT, "mod.py"), {})
        assert "'amount'" in violation.message

    def test_matching_docstring_is_clean(self, bind_source):
        source = GOOGLE_DRIFT.replace("amount:", "factor:")
        assert docstring_drift(bind_source(source, "mod.py"), {}) == []

    def test_undocumented_param_needs_require_complete(self, bind_source):
        source = (
            "def scale(value, factor):\n"
            '    """Scale.\n\n    Args:\n        value: The value.\n        factor: F.\n        amount: A.\n    """\n'
            "    return value * factor\n"
        )
        # 'amount' is a ghost; with require-complete nothing extra fires
        # because every signature param is documented.
        index = bind_source(source, "mod.py")
        assert len(docstring_drift(index, {})) == 1

        drifted = bind_source(GOOGLE_DRIFT, "mod.py")
        default_messages = [v.message for v in docstring_drift(drifted, {})]
        assert not any("does not document" in m for m in default_messages)
        complete = docstring_drift(drifted, {"require-complete": True})
        assert any(
            "does not document parameter 'factor'" in v.message for v in complete
        )

    def test_no_section_never_demands_documentation(self, bind_source):
        source = (
            "def scale(value, factor):\n"
            '    """Just a summary."""\n'
            "    return value * factor\n"
        )
        index = bind_source(source, "mod.py")
        assert docstring_drift(index, {"require-complete": True}) == []

    def test_stars_normalize_against_signature(self, bind_source):
        source = (
            "def collect(*args, **kwargs):\n"
            '    """Collect.\n\n    Args:\n        *args: Positional.\n        kwargs: Keyword, documented bare.\n    """\n'
            "    return args, kwargs\n"
        )
        assert docstring_drift(bind_source(source, "mod.py"), {}) == []

    def test_self_and_cls_are_skipped(self, bind_source):
        source = (
            "class Svc:\n"
            "    def run(self, value):\n"
            '        """Run.\n\n        Args:\n            value: V.\n        """\n'
            "        return value\n"
        )
        index = bind_source(source, "mod.py")
        assert docstring_drift(index, {"require-complete": True}) == []

    def test_forced_style_option_suppresses_other_styles(self, bind_source):
        index = bind_source(GOOGLE_DRIFT, "mod.py")
        assert docstring_drift(index, {"style": "sphinx"}) == []
        assert len(docstring_drift(index, {"style": "google"})) == 1

    def test_allow_option_exempts_symbol(self, bind_source):
        index = bind_source(GOOGLE_DRIFT, "mod.py")
        assert docstring_drift(index, {"allow": ["mod:scale"]}) == []
        assert docstring_drift(index, {"allow": ["other:*"]}) != []


# ---------------------------------------------------------------------------
# Fix attachment and planning
# ---------------------------------------------------------------------------


class TestDocstringParamRenameFix:
    def test_renameable_drift_carries_fix(self, bind_source):
        [violation] = docstring_drift(bind_source(GOOGLE_DRIFT, "mod.py"), {})
        assert isinstance(violation.fix, DocstringParamRenameFix)
        assert violation.fix.fix_id == (
            "docstring-drift:rename-param:mod:scale:amount"
        )
        assert violation.fix.new_param == "factor"

    def test_two_undocumented_params_is_report_only(self, bind_source):
        source = (
            "def scale(value, factor, base):\n"
            '    """Scale.\n\n    Args:\n        value: The value.\n        amount: A.\n    """\n'
            "    return value * factor * base\n"
        )
        [violation] = docstring_drift(bind_source(source, "mod.py"), {})
        assert violation.fix is None  # which param was renamed is ambiguous

    def test_two_ghosts_is_report_only(self, bind_source):
        source = (
            "def scale(value, factor):\n"
            '    """Scale.\n\n    Args:\n        value: The value.\n        amount: A.\n        extra: E.\n    """\n'
            "    return value * factor\n"
        )
        violations = docstring_drift(bind_source(source, "mod.py"), {})
        assert len(violations) == 2
        assert all(v.fix is None for v in violations)

    def test_rename_repair_end_to_end(self, indexed_project):
        project_dir, store = indexed_project({"mod.py": GOOGLE_DRIFT})
        [violation] = docstring_drift(store.load("mod.py"), {})
        plan = violation.fix.plan(store)
        assert isinstance(plan, FixPlan)

        _apply_plan(project_dir, store, plan)
        assert (project_dir / "mod.py").read_text() == GOOGLE_DRIFT.replace(
            "amount: The multiplier.", "factor: The multiplier."
        )

    def test_sphinx_repair_end_to_end(self, indexed_project):
        project_dir, store = indexed_project({"mod.py": SPHINX_DRIFT})
        [violation] = docstring_drift(store.load("mod.py"), {})
        plan = violation.fix.plan(store)
        assert isinstance(plan, FixPlan)

        _apply_plan(project_dir, store, plan)
        assert (project_dir / "mod.py").read_text() == SPHINX_DRIFT.replace(
            ":param int amount:", ":param int factor:"
        )

    def test_repeated_token_in_docstring_declines_ambiguous(self, indexed_project):
        source = (
            "def scale(value, factor):\n"
            '    """Scale.\n\n    Args:\n        value: The value.\n        amount: The amount used.\n    """\n'
            "    return value * factor\n"
        )
        _, store = indexed_project({"mod.py": source})
        [violation] = docstring_drift(store.load("mod.py"), {})
        declined = violation.fix.plan(store)
        assert isinstance(declined, FixDeclined)
        assert declined.reason is DeclineReason.AMBIGUOUS
        assert "occurs 2 times" in declined.detail

    def test_mutated_file_declines_stale(self, indexed_project):
        project_dir, store = indexed_project({"mod.py": GOOGLE_DRIFT})
        [violation] = docstring_drift(store.load("mod.py"), {})
        (project_dir / "mod.py").write_text("# shifted\n" + GOOGLE_DRIFT)

        declined = violation.fix.plan(store)
        assert isinstance(declined, FixDeclined)
        assert declined.reason is DeclineReason.STALE_INDEX

    def test_drift_shape_changed_declines(self, indexed_project):
        _, store = indexed_project({"mod.py": GOOGLE_DRIFT})
        # A hand-built fix whose expectations no longer match the drift.
        fix = DocstringParamRenameFix(
            file_path="mod.py",
            symbol_id="mod:scale",
            old_param="amount",
            new_param="other",
            style="google",
        )
        declined = fix.plan(store)
        assert isinstance(declined, FixDeclined)
        assert declined.reason is DeclineReason.TEXT_MISMATCH

    def test_style_mismatch_declines(self, indexed_project):
        _, store = indexed_project({"mod.py": GOOGLE_DRIFT})
        fix = DocstringParamRenameFix(
            file_path="mod.py",
            symbol_id="mod:scale",
            old_param="amount",
            new_param="factor",
            style="numpy",
        )
        declined = fix.plan(store)
        assert isinstance(declined, FixDeclined)
        assert declined.reason is DeclineReason.TEXT_MISMATCH
        assert "numpy" in declined.detail


# ---------------------------------------------------------------------------
# check --fix CLI
# ---------------------------------------------------------------------------


class TestDocstringDriftCli:
    def _project(self, tmp_path: Path, runner: CliRunner, source: str) -> Path:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "test"\n'
            "[tool.pypeeker]\n"
            'src = ["src"]\n'
            'rules = ["docstring-drift"]\n'
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "mod.py").write_text(source)
        os.chdir(tmp_path)
        result = runner.invoke(
            main, ["index", str(tmp_path / "src")], catch_exceptions=False
        )
        assert result.exit_code == 0, result.output
        return tmp_path

    def test_check_fix_repairs_renamed_param(self, tmp_path):
        runner = CliRunner()
        project = self._project(tmp_path, runner, GOOGLE_DRIFT)

        result = runner.invoke(main, ["check", "--fix"], catch_exceptions=False)
        report = json.loads(result.output)

        assert [a["fix_id"] for a in report["applied"]] == [
            "docstring-drift:rename-param:mod:scale:amount"
        ]
        assert report["declined"] == []
        assert result.exit_code == 0, result.output
        assert (project / "src" / "mod.py").read_text() == GOOGLE_DRIFT.replace(
            "amount: The multiplier.", "factor: The multiplier."
        )

    def test_check_reports_ambiguous_drift_without_fixing(self, tmp_path):
        runner = CliRunner()
        source = (
            "def scale(value, factor, base):\n"
            '    """Scale.\n\n    Args:\n        value: The value.\n        amount: A.\n    """\n'
            "    return value * factor * base\n"
        )
        project = self._project(tmp_path, runner, source)

        result = runner.invoke(main, ["check", "--fix"], catch_exceptions=False)
        report = json.loads(result.output)

        assert report["applied"] == []
        assert report["residual_violations"] == 1
        assert result.exit_code == 1
        assert (project / "src" / "mod.py").read_text() == source
