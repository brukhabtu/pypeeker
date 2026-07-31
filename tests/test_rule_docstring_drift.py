"""Tests for the docstring-drift rule and its rename repair (TASK-93).

Covers the three style parsers (documented param sets, absent/undocumented
detection), stars normalization, style autodetection and forcing, the
require-complete gate, the allow option, the conservative remedy (single
undocumented param renamed end-to-end, every ambiguity declined), and the
``check --fix`` CLI path.

Since TASK-124 the repair is a
:class:`~pypeeker.intents.RenameDocstringParamIntent` on
``Violation.remedy``, executed by
:class:`~pypeeker.refactor.docstring_ops.DocstringParamRenamePlanner`. The
decline vocabulary is unchanged from the fix protocol it ports and the tests
below pin every slug: the planner is index-anchored, so it refuses
``stale-index`` on a file edited since it was indexed, re-derives the drift
from the current docstring (``text-mismatch`` when it changed shape or the
forced style no longer parses), and refuses ``ambiguous`` when the stale name
occurs more than once. The rule attaches the remedy for the whole rename
*shape* (one stale name, one undocumented parameter) and leaves those finer
judgements to the planner, so a refusal is reported under ``declined``
instead of silently withheld.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from pypeeker.analysis import parse_documented_params
from pypeeker.app.submit import SubmitError, submit_intent
from pypeeker.check.builtin.docstring_drift import _docstring_drift as docstring_drift
from pypeeker.cli import main
from pypeeker.intents import RenameDocstringParamIntent
from pypeeker.models.transaction import TransactionHeader
from pypeeker.refactor.applier import TransactionApplier
from pypeeker.refactor.registry import Materialized
from pypeeker.storage import TransactionStore


def _plan(store, remedy) -> Materialized:
    """Materialize a remedy through its registered planner (as ``check --fix`` does)."""
    with tempfile.TemporaryDirectory() as scratch:
        return submit_intent(remedy, store, TransactionStore(Path(scratch)))


def _decline(store, remedy) -> SubmitError:
    """The :class:`SubmitError` a remedy's planner refuses with."""
    with pytest.raises(SubmitError) as excinfo:
        _plan(store, remedy)
    return excinfo.value


def _apply_plan(project_dir, store, plan: Materialized, tx_id: str = "fix-tx") -> None:
    """Apply materialized edits through the standard transaction machinery."""
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


class TestDocstringParamRenameRemedy:
    def test_renameable_drift_carries_remedy(self, bind_source):
        [violation] = docstring_drift(bind_source(GOOGLE_DRIFT, "mod.py"), {})
        assert isinstance(violation.remedy, RenameDocstringParamIntent)
        assert violation.remedy.intent_id == (
            "docstring-drift:rename-param:mod:scale:amount"
        )
        assert violation.remedy.symbol_id == "mod:scale"
        assert violation.remedy.old_param == "amount"
        assert violation.remedy.new_param == "factor"
        assert violation.remedy.style == "google"
        # The phrasing check --fix reports next to the repair.
        assert violation.remedy.description == (
            "rename documented parameter 'amount' to 'factor' in the "
            "docstring of 'mod:scale'"
        )

    def test_sphinx_remedy_carries_the_detected_style(self, bind_source):
        [violation] = docstring_drift(bind_source(SPHINX_DRIFT, "mod.py"), {})
        assert violation.remedy.style == "sphinx"
        assert violation.remedy.old_param == "amount"

    def test_numpy_remedy_carries_the_detected_style(self, bind_source):
        [violation] = docstring_drift(bind_source(NUMPY_DRIFT, "mod.py"), {})
        assert violation.remedy.style == "numpy"
        assert violation.remedy.old_param == "amount"

    def test_two_undocumented_params_is_report_only(self, bind_source):
        source = (
            "def scale(value, factor, base):\n"
            '    """Scale.\n\n    Args:\n        value: The value.\n        amount: A.\n    """\n'
            "    return value * factor * base\n"
        )
        [violation] = docstring_drift(bind_source(source, "mod.py"), {})
        assert violation.remedy is None  # which param was renamed is ambiguous

    def test_two_ghosts_is_report_only(self, bind_source):
        source = (
            "def scale(value, factor):\n"
            '    """Scale.\n\n    Args:\n        amount: A.\n        rate: B.\n    """\n'
            "    return value * factor\n"
        )
        violations = docstring_drift(bind_source(source, "mod.py"), {})
        assert len(violations) == 2
        assert all(v.remedy is None for v in violations)

    def test_rename_repair_end_to_end(self, indexed_project):
        project_dir, store = indexed_project({"mod.py": GOOGLE_DRIFT})
        [violation] = docstring_drift(store.load("mod.py"), {})
        plan = _plan(store, violation.remedy)
        # Only the stale name token is rewritten, not the whole entry line.
        assert [(e.old, e.new) for e in plan.edits] == [("amount", "factor")]

        _apply_plan(project_dir, store, plan)
        assert (project_dir / "mod.py").read_text() == GOOGLE_DRIFT.replace(
            "amount: The multiplier.", "factor: The multiplier."
        )

    def test_sphinx_repair_end_to_end(self, indexed_project):
        project_dir, store = indexed_project({"mod.py": SPHINX_DRIFT})
        [violation] = docstring_drift(store.load("mod.py"), {})
        plan = _plan(store, violation.remedy)

        _apply_plan(project_dir, store, plan)
        assert (project_dir / "mod.py").read_text() == SPHINX_DRIFT.replace(
            ":param int amount:", ":param int factor:"
        )

    def test_repeated_token_in_docstring_declines_ambiguous(self, indexed_project):
        # Rewriting one of two occurrences is unsafe. The rule still attaches
        # the remedy (the rename shape holds); the planner is what refuses, so
        # the finding is reported under `declined` rather than vanishing.
        source = (
            "def scale(value, factor):\n"
            '    """Scale.\n\n    Args:\n        value: The value.\n        amount: The amount used.\n    """\n'
            "    return value * factor\n"
        )
        _, store = indexed_project({"mod.py": source})
        [violation] = docstring_drift(store.load("mod.py"), {})
        assert violation.remedy is not None

        declined = _decline(store, violation.remedy)
        assert declined.code == "ambiguous"
        assert "occurs 2 times" in declined.detail

    def test_mutated_file_declines_stale(self, indexed_project):
        # The repair is index-anchored: a file edited since it was indexed is
        # refused outright, even when the edit is benign (a leading comment
        # that leaves the drifted docstring line intact). Re-index and re-plan.
        project_dir, store = indexed_project({"mod.py": GOOGLE_DRIFT})
        [violation] = docstring_drift(store.load("mod.py"), {})
        (project_dir / "mod.py").write_text("# shifted\n" + GOOGLE_DRIFT)

        declined = _decline(store, violation.remedy)
        assert declined.code == "stale-index"
        assert "re-index and re-plan" in declined.detail

    def test_user_already_fixed_the_drift_declines_stale(self, indexed_project):
        # The user resolved the drift themselves by renaming the parameter to
        # match the docstring. Replanning must not reintroduce it.
        project_dir, store = indexed_project({"mod.py": GOOGLE_DRIFT})
        [violation] = docstring_drift(store.load("mod.py"), {})
        (project_dir / "mod.py").write_text(
            GOOGLE_DRIFT.replace("def scale(value, factor)", "def scale(value, amount)")
        )

        assert _decline(store, violation.remedy).code == "stale-index"

    def test_drift_shape_changed_declines(self, indexed_project):
        # A hand-built remedy whose expectations no longer match the drift:
        # the planner re-derives it from the current docstring and refuses.
        _, store = indexed_project({"mod.py": GOOGLE_DRIFT})
        remedy = RenameDocstringParamIntent(
            "docstring-drift:rename-param:mod:scale:amount",
            symbol_id="mod:scale",
            old_param="amount",
            new_param="other",
            style="google",
        )
        declined = _decline(store, remedy)
        assert declined.code == "text-mismatch"
        assert "the drift changed" in declined.detail

    def test_style_mismatch_declines(self, indexed_project):
        _, store = indexed_project({"mod.py": GOOGLE_DRIFT})
        remedy = RenameDocstringParamIntent(
            "docstring-drift:rename-param:mod:scale:amount",
            symbol_id="mod:scale",
            old_param="amount",
            new_param="factor",
            style="numpy",
        )
        declined = _decline(store, remedy)
        assert declined.code == "text-mismatch"
        assert "numpy" in declined.detail

    def test_unknown_symbol_declines(self, indexed_project):
        _, store = indexed_project({"mod.py": GOOGLE_DRIFT})
        remedy = RenameDocstringParamIntent(
            "docstring-drift:rename-param:mod:gone:amount",
            symbol_id="mod:gone",
            old_param="amount",
            new_param="factor",
            style="google",
        )
        assert _decline(store, remedy).code == "text-mismatch"

    def test_missing_file_declines(self, indexed_project):
        project_dir, store = indexed_project({"mod.py": GOOGLE_DRIFT})
        [violation] = docstring_drift(store.load("mod.py"), {})
        (project_dir / "mod.py").unlink()

        assert _decline(store, violation.remedy).code == "file-missing"


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

        assert [a["fix_id"] for a in report["fixes"]] == [
            "docstring-drift:rename-param:mod:scale:amount"
        ]
        assert report["fixes"][0]["description"] == (
            "rename documented parameter 'amount' to 'factor' in the "
            "docstring of 'mod:scale'"
        )
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

        assert report["fixes"] == []
        assert report["residual_violations"] == 1
        assert result.exit_code == 1
        assert (project / "src" / "mod.py").read_text() == source

    def test_check_fix_reports_repeated_token_as_declined(self, tmp_path):
        # The rename shape holds, so a repair IS proposed; the planner refuses
        # it because the stale name occurs twice. `declined` is how a consumer
        # learns why the finding was not auto-fixed, so it must carry the
        # reason rather than the finding disappearing from the report.
        runner = CliRunner()
        source = (
            "def scale(value, factor):\n"
            '    """Scale.\n\n    Args:\n        value: The value.\n        amount: The amount used.\n    """\n'
            "    return value * factor\n"
        )
        project = self._project(tmp_path, runner, source)

        result = runner.invoke(main, ["check", "--fix"], catch_exceptions=False)
        report = json.loads(result.output)

        assert report["fixes"] == []
        assert report["declined"] == [
            {
                "fix_id": "docstring-drift:rename-param:mod:scale:amount",
                "reason": "ambiguous",
                "detail": (
                    "'amount' occurs 2 times in the docstring; rewriting one "
                    "occurrence is unsafe"
                ),
            }
        ]
        assert result.exit_code == 1
        assert (project / "src" / "mod.py").read_text() == source
