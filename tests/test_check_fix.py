"""Tests for ``check --fix`` and the first three autofixes (TASK-84).

Covers each fix end-to-end (file content asserted after apply), the
conservative decline paths (ambiguous bracket scans, decorated symbols,
files mutated between detection and plan), deterministic conflict skipping,
rollback of a check-fix transaction, the DECLARED-confidence gate, the
baseline flag conflicts, and the --update-baseline symbols-namespace
re-seed (born-private interplay).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from click.testing import CliRunner

from pypeeker.check.baseline import clear_symbol_baseline
from pypeeker.check.builtin.unused_imports import _unused_imports as unused_imports
from pypeeker.check.context import CheckContext
from pypeeker.check.fixes import (
    DeclineReason,
    DeleteUnusedSymbolFix,
    FixDeclined,
    FixPlan,
    PreferTupleFix,
    RemoveUnusedImportFix,
)
from pypeeker.check.rules import prefer_tuple, unused_public_symbol
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


def _fix_project(tmp_path: Path, runner: CliRunner, files: dict[str, str],
                 rules: str, extra: str = "") -> Path:
    """tmp project with the given rules enabled and src files indexed."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "test"\n'
        "[tool.pypeeker]\n"
        'src = ["src"]\n'
        f"rules = {rules}\n"
        f"{extra}"
    )
    for name, content in files.items():
        p = tmp_path / "src" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    os.chdir(tmp_path)
    result = runner.invoke(
        main, ["index", str(tmp_path / "src")], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    return tmp_path


# ---------------------------------------------------------------------------
# prefer-tuple fix
# ---------------------------------------------------------------------------


class TestPreferTupleFix:
    def test_rule_attaches_fix(self, indexed_project):
        _, store = indexed_project(
            {"mod.py": "def f():\n    xs = [1, 2]\n    return xs\n"}
        )
        [violation] = prefer_tuple(store.load("mod.py"), {})
        assert isinstance(violation.fix, PreferTupleFix)
        assert violation.fix.fix_id == "prefer-tuple:tuplify:mod:f:xs"

    def test_multi_element_rewrite_end_to_end(self, indexed_project):
        project_dir, store = indexed_project(
            {"mod.py": "def f():\n    xs = [1, 2, 3]\n    return xs\n"}
        )
        [violation] = prefer_tuple(store.load("mod.py"), {})
        plan = violation.fix.plan(store)
        assert isinstance(plan, FixPlan)

        _apply_plan(project_dir, store, plan)
        assert (
            project_dir / "mod.py"
        ).read_text() == "def f():\n    xs = (1, 2, 3)\n    return xs\n"

    def test_single_element_list_gets_trailing_comma(self, indexed_project):
        project_dir, store = indexed_project(
            {"mod.py": "def f(x):\n    xs = [x]\n    return xs\n"}
        )
        [violation] = prefer_tuple(store.load("mod.py"), {})
        plan = violation.fix.plan(store)
        assert isinstance(plan, FixPlan)

        _apply_plan(project_dir, store, plan)
        # (x) would just be x — the closing bracket must become ",)".
        assert (
            project_dir / "mod.py"
        ).read_text() == "def f(x):\n    xs = (x,)\n    return xs\n"

    def test_multiline_literal_with_strings_and_nesting(self, indexed_project):
        source = (
            "def f():\n"
            "    xs = [\n"
            "        'a[b]',  # bracket inside a string and a comment ]\n"
            "        [1, 2],\n"
            "    ]\n"
            "    return xs\n"
        )
        project_dir, store = indexed_project({"mod.py": source})
        [violation] = prefer_tuple(store.load("mod.py"), {})
        plan = violation.fix.plan(store)
        assert isinstance(plan, FixPlan)

        _apply_plan(project_dir, store, plan)
        assert (project_dir / "mod.py").read_text() == source.replace(
            "xs = [", "xs = ("
        ).replace("    ]\n    return", "    )\n    return")

    def test_fstring_in_literal_declines_ambiguous(self, indexed_project):
        _, store = indexed_project(
            {"mod.py": 'def f(x):\n    xs = [f"{x}", 1]\n    return xs\n'}
        )
        [violation] = prefer_tuple(store.load("mod.py"), {})
        declined = violation.fix.plan(store)

        assert isinstance(declined, FixDeclined)
        assert declined.reason is DeclineReason.AMBIGUOUS
        assert "f-string" in declined.detail

    def test_mutated_file_between_detect_and_plan_declines_stale(
        self, indexed_project
    ):
        project_dir, store = indexed_project(
            {"mod.py": "def f():\n    xs = [1, 2]\n    return xs\n"}
        )
        [violation] = prefer_tuple(store.load("mod.py"), {})
        # Mutate the file WITHOUT re-indexing: the index no longer
        # describes the bytes on disk, so re-locating through it is unsafe.
        (project_dir / "mod.py").write_text(
            "# moved\ndef f():\n    xs = [1, 2]\n    return xs\n"
        )

        declined = violation.fix.plan(store)

        assert isinstance(declined, FixDeclined)
        assert declined.reason is DeclineReason.STALE_INDEX

    def test_missing_file_declines(self, indexed_project):
        project_dir, store = indexed_project(
            {"mod.py": "def f():\n    xs = [1]\n    return xs\n"}
        )
        [violation] = prefer_tuple(store.load("mod.py"), {})
        (project_dir / "mod.py").unlink()

        declined = violation.fix.plan(store)
        assert isinstance(declined, FixDeclined)
        assert declined.reason is DeclineReason.FILE_MISSING


# ---------------------------------------------------------------------------
# unused-imports rule + fix
# ---------------------------------------------------------------------------


class TestUnusedImportsRule:
    def test_flags_only_unused_bindings(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "import os\n"
                "from typing import Any, Optional\n"
                "\n"
                "def f(x: Any):\n"
                "    return x\n"
            )
        })
        violations = unused_imports(store.load("mod.py"), {})
        assert [v.message for v in violations] == [
            "import 'os' is unused in this module",
            "import 'Optional' is unused in this module",
        ]
        assert all(isinstance(v.fix, RemoveUnusedImportFix) for v in violations)

    def test_skips_init_future_underscore_and_all_files(self, indexed_project):
        _, store = indexed_project({
            "pkg/__init__.py": "from pkg.mod import helper\n",
            "pkg/mod.py": (
                "from __future__ import annotations\n"
                "import os as _os\n"
                "\n"
                "def helper():\n"
                "    return 1\n"
            ),
            "pkg/exported.py": (
                "import os\n"
                "\n"
                "__all__ = ['os']\n"
            ),
        })
        assert unused_imports(store.load("pkg/__init__.py"), {}) == []
        assert unused_imports(store.load("pkg/mod.py"), {}) == []
        assert unused_imports(store.load("pkg/exported.py"), {}) == []

    def test_dynamic_access_downgrades_confidence(self, indexed_project):
        from pypeeker.models.capabilities import Confidence

        _, store = indexed_project({
            "mod.py": "import os\n\ndef f():\n    return globals()\n"
        })
        [violation] = unused_imports(store.load("mod.py"), {})
        assert violation.confidence is Confidence.HEURISTIC

    def test_single_name_line_deleted_whole(self, indexed_project):
        project_dir, store = indexed_project({
            "mod.py": "import os\n\ndef f():\n    return 1\n"
        })
        [violation] = unused_imports(store.load("mod.py"), {})
        plan = violation.fix.plan(store)
        assert isinstance(plan, FixPlan)

        _apply_plan(project_dir, store, plan)
        assert (project_dir / "mod.py").read_text() == "\ndef f():\n    return 1\n"

    def test_multi_name_first_entry_removed(self, indexed_project):
        project_dir, store = indexed_project({
            "mod.py": "import os, sys\n\ndef f():\n    return sys.argv\n"
        })
        [violation] = unused_imports(store.load("mod.py"), {})
        plan = violation.fix.plan(store)
        assert isinstance(plan, FixPlan)

        _apply_plan(project_dir, store, plan)
        assert (
            project_dir / "mod.py"
        ).read_text() == "import sys\n\ndef f():\n    return sys.argv\n"

    def test_multi_name_last_entry_removed(self, indexed_project):
        project_dir, store = indexed_project({
            "mod.py": (
                "from typing import Any, Optional\n"
                "\n"
                "def f(x: Any):\n"
                "    return x\n"
            )
        })
        violations = unused_imports(store.load("mod.py"), {})
        [violation] = [v for v in violations if "'Optional'" in v.message]
        plan = violation.fix.plan(store)
        assert isinstance(plan, FixPlan)

        _apply_plan(project_dir, store, plan)
        assert (
            project_dir / "mod.py"
        ).read_text() == "from typing import Any\n\ndef f(x: Any):\n    return x\n"

    def test_parenthesized_import_list_declines(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "from typing import (Any, Optional)\n"
                "\n"
                "def f(x: Any):\n"
                "    return x\n"
            )
        })
        violations = unused_imports(store.load("mod.py"), {})
        [violation] = [v for v in violations if "'Optional'" in v.message]

        declined = violation.fix.plan(store)
        assert isinstance(declined, FixDeclined)
        assert declined.reason is DeclineReason.AMBIGUOUS

    def test_multiline_parenthesized_import_declines(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "from typing import (\n"
                "    Optional,\n"
                ")\n"
                "\n"
                "def f():\n"
                "    return 1\n"
            )
        })
        [violation] = unused_imports(store.load("mod.py"), {})
        # The bound name sits on a continuation line, not on an import
        # statement line the fix can edit safely: conservative decline.
        assert isinstance(violation.fix.plan(store), FixDeclined)


# ---------------------------------------------------------------------------
# unused-public-symbol --also-private deletion fix
# ---------------------------------------------------------------------------


class TestDeleteUnusedSymbolFix:
    def _violations(self, store, options=None):
        if options is None:
            options = {"also-private": True}
        indexes = [store.load(p) for p in store.list_indexed_files()]
        context = CheckContext(store, indexes)
        return unused_public_symbol(context, options)

    def test_private_finding_carries_fix_public_does_not(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def visible():\n"
                "    return 1\n"
                "\n"
                "\n"
                "def _dead():\n"
                "    return 2\n"
            )
        })
        violations = self._violations(store)
        by_name = {v.message.split("'")[1]: v for v in violations}
        assert set(by_name) == {"mod:visible", "mod:_dead"}
        # Public API stays human-decided.
        assert by_name["mod:visible"].fix is None
        assert isinstance(by_name["mod:_dead"].fix, DeleteUnusedSymbolFix)

    def test_default_options_keep_public_only_behavior(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "def _dead():\n    return 2\n"
        })
        assert self._violations(store, options={}) == []

    def test_deletion_end_to_end_eats_trailing_blank_lines(self, indexed_project):
        project_dir, store = indexed_project({
            "mod.py": (
                "def _dead():\n"
                "    return 1\n"
                "\n"
                "\n"
                "def keep():\n"
                "    return keep\n"
            )
        })
        violations = self._violations(store)
        [violation] = [v for v in violations if "_dead" in v.message]
        plan = violation.fix.plan(store)
        assert isinstance(plan, FixPlan)

        _apply_plan(project_dir, store, plan)
        assert (
            project_dir / "mod.py"
        ).read_text() == "def keep():\n    return keep\n"

    def test_class_deletion(self, indexed_project):
        project_dir, store = indexed_project({
            "mod.py": (
                "class _Dead:\n"
                "    x = 1\n"
                "\n"
                "\n"
                "VALUE = 2\n"
            )
        })
        violations = self._violations(
            store, options={"also-private": True}
        )
        [violation] = [v for v in violations if "_Dead" in v.message]
        plan = violation.fix.plan(store)
        assert isinstance(plan, FixPlan)

        _apply_plan(project_dir, store, plan)
        assert (project_dir / "mod.py").read_text() == "VALUE = 2\n"

    def test_decorated_symbol_declines(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "import functools\n"
                "\n"
                "\n"
                "@functools.cache\n"
                "def _dead():\n"
                "    return 1\n"
            )
        })
        violations = self._violations(store)
        [violation] = [v for v in violations if "_dead" in v.message]

        declined = violation.fix.plan(store)
        assert isinstance(declined, FixDeclined)
        assert declined.reason is DeclineReason.AMBIGUOUS
        assert "decorated" in declined.detail

    def test_mutated_file_declines_stale(self, indexed_project):
        project_dir, store = indexed_project({
            "mod.py": "def _dead():\n    return 1\n"
        })
        violations = self._violations(store)
        [violation] = violations
        (project_dir / "mod.py").write_text("# shifted\ndef _dead():\n    return 1\n")

        declined = violation.fix.plan(store)
        assert isinstance(declined, FixDeclined)
        assert declined.reason is DeclineReason.STALE_INDEX


# ---------------------------------------------------------------------------
# check --fix CLI
# ---------------------------------------------------------------------------

COMBINED_SOURCE = (
    "import os\n"
    "from typing import Any, Optional\n"
    "\n"
    "\n"
    "def use(x: Any):\n"
    "    xs = [1, 2, 3]\n"
    "    return xs\n"
    "\n"
    "\n"
    "def _dead():\n"
    "    return 1\n"
    "\n"
    "\n"
    "def keep():\n"
    "    return use(1)\n"
)

COMBINED_FIXED = (
    "from typing import Any\n"
    "\n"
    "\n"
    "def use(x: Any):\n"
    "    xs = (1, 2, 3)\n"
    "    return xs\n"
    "\n"
    "\n"
    "def keep():\n"
    "    return use(1)\n"
)

ALL_FIX_RULES = '["prefer-tuple", "unused-imports", "unused-public-symbol"]'
ALSO_PRIVATE = "[tool.pypeeker.unused-public-symbol]\nalso-private = true\n"


class TestCheckFixCli:
    def test_all_three_fixes_apply_in_one_transaction(self, tmp_path):
        runner = CliRunner()
        project = _fix_project(
            tmp_path, runner, {"mod.py": COMBINED_SOURCE},
            rules=ALL_FIX_RULES, extra=ALSO_PRIVATE,
        )

        result = runner.invoke(main, ["check", "--fix"], catch_exceptions=False)
        report = json.loads(result.output)

        assert [a["fix_id"] for a in report["applied"]] == [
            "unused-imports:remove:mod:os",
            "unused-imports:remove:mod:Optional",
            "prefer-tuple:tuplify:mod:use:xs",
            "unused-symbol:delete:mod:_dead",
        ]
        assert report["skipped_conflicts"] == []
        assert report["declined"] == []
        assert report["tx_id"]
        # 'keep' is public-unused and carries no fix: it remains.
        assert report["residual_violations"] == 1
        assert result.exit_code == 1
        assert (project / "src" / "mod.py").read_text() == COMBINED_FIXED

        # One transaction, applied, holding every edit.
        tx = runner.invoke(
            main, ["transactions", "show", report["tx_id"]], catch_exceptions=False
        )
        shown = json.loads(tx.output)
        assert shown["header"]["operation"] == "check-fix"
        assert shown["header"]["status"] == "applied"

    def test_overlapping_fixes_skipped_deterministically(self, tmp_path):
        runner = CliRunner()
        project = _fix_project(
            tmp_path, runner,
            {"mod.py": "def _dead():\n    xs = [1, 2]\n    return xs\n"},
            rules=ALL_FIX_RULES, extra=ALSO_PRIVATE,
        )

        result = runner.invoke(main, ["check", "--fix"], catch_exceptions=False)
        report = json.loads(result.output)

        # The deletion starts earlier in the file, so it wins; the tuple
        # rewrite targets bytes inside the deleted range and is skipped.
        assert [a["fix_id"] for a in report["applied"]] == [
            "unused-symbol:delete:mod:_dead"
        ]
        assert [s["fix_id"] for s in report["skipped_conflicts"]] == [
            "prefer-tuple:tuplify:mod:_dead:xs"
        ]
        assert result.exit_code == 0, result.output
        assert (project / "src" / "mod.py").read_text() == ""

    def test_rollback_restores_pre_fix_bytes(self, tmp_path):
        runner = CliRunner()
        project = _fix_project(
            tmp_path, runner, {"mod.py": COMBINED_SOURCE},
            rules=ALL_FIX_RULES, extra=ALSO_PRIVATE,
        )
        result = runner.invoke(main, ["check", "--fix"], catch_exceptions=False)
        report = json.loads(result.output)
        assert (project / "src" / "mod.py").read_text() == COMBINED_FIXED

        rolled = runner.invoke(
            main, ["rollback", report["tx_id"]], catch_exceptions=False
        )
        assert rolled.exit_code == 0, rolled.output
        assert (project / "src" / "mod.py").read_text() == COMBINED_SOURCE

    def test_heuristic_confidence_fixes_are_not_applied(self, tmp_path):
        runner = CliRunner()
        project = _fix_project(
            tmp_path, runner,
            {"mod.py": "import os\n\n\ndef f():\n    return globals()\n"},
            rules='["unused-imports"]',
        )

        result = runner.invoke(
            main, ["check", "--fix", "--strict"], catch_exceptions=False
        )
        report = json.loads(result.output)

        assert report["applied"] == []
        assert report["tx_id"] is None
        # The heuristic finding still exists; it just never auto-fixes.
        assert report["residual_violations"] == 1
        assert "import os" in (project / "src" / "mod.py").read_text()

    def test_declined_fixes_are_reported(self, tmp_path):
        runner = CliRunner()
        _fix_project(
            tmp_path, runner,
            {"mod.py": 'def f(x):\n    xs = [f"{x}"]\n    return xs\n'},
            rules='["prefer-tuple"]',
        )

        result = runner.invoke(main, ["check", "--fix"], catch_exceptions=False)
        report = json.loads(result.output)

        assert report["applied"] == []
        [declined] = report["declined"]
        assert declined["fix_id"] == "prefer-tuple:tuplify:mod:f:xs"
        assert declined["reason"] == "ambiguous"
        assert result.exit_code == 1  # the violation remains

    def test_clean_project_fix_is_a_no_op(self, tmp_path):
        runner = CliRunner()
        _fix_project(
            tmp_path, runner,
            {"mod.py": "import os\n\n\ndef f():\n    return os.getcwd()\n"},
            rules='["unused-imports"]',
        )

        result = runner.invoke(main, ["check", "--fix"], catch_exceptions=False)
        report = json.loads(result.output)
        assert result.exit_code == 0
        assert report == {
            "applied": [],
            "skipped_conflicts": [],
            "declined": [],
            "residual_violations": 0,
            "tx_id": None,
        }

    def test_fix_conflicts_with_baseline_flags(self, tmp_path):
        runner = CliRunner()
        _fix_project(
            tmp_path, runner, {"mod.py": "x = 1\n"}, rules='["unused-imports"]'
        )
        for flags in (["--fix", "--baseline"], ["--fix", "--update-baseline"]):
            result = runner.invoke(main, ["check", *flags])
            assert result.exit_code != 0
            assert "--fix cannot be combined" in result.output


# ---------------------------------------------------------------------------
# --update-baseline symbols namespace (TASK-99 follow-up)
# ---------------------------------------------------------------------------


class TestUpdateBaselineSymbols:
    def test_update_baseline_refreshes_symbol_namespace(self, tmp_path):
        runner = CliRunner()
        project = _fix_project(
            tmp_path, runner,
            {"m.py": "def legacy():\n    return 1\n"},
            rules='["born-private"]',
        )
        baseline = project / ".semantic-tool" / "check-baseline.json"

        # First run self-seeds the symbols namespace silently.
        first = runner.invoke(main, ["check"], catch_exceptions=False)
        assert first.exit_code == 0, first.output
        assert json.loads(baseline.read_text())["symbols"] == ["m:legacy"]

        # A newly public, module-local symbol fires born-private.
        (project / "src" / "m.py").write_text(
            "def legacy():\n    return 1\n\n\ndef newcomer():\n    return legacy()\n"
        )
        flagged = runner.invoke(main, ["check"], catch_exceptions=False)
        assert flagged.exit_code == 1
        assert "newly public 'newcomer'" in flagged.output

        # --update-baseline re-seeds the namespace with the current surface.
        update = runner.invoke(
            main, ["check", "--update-baseline"], catch_exceptions=False
        )
        assert update.exit_code == 0, update.output
        data = json.loads(baseline.read_text())
        assert data["symbols"] == ["m:legacy", "m:newcomer"]
        assert "violations" in data  # both namespaces coexist

        accepted = runner.invoke(main, ["check"], catch_exceptions=False)
        assert accepted.exit_code == 0, accepted.output

    def test_update_baseline_without_born_private_keeps_symbols(self, tmp_path):
        runner = CliRunner()
        project = _fix_project(
            tmp_path, runner,
            {"m.py": "def foo():\n    return 1\n"},
            rules='["require-docstrings"]',
        )
        baseline = project / ".semantic-tool" / "check-baseline.json"
        baseline.parent.mkdir(exist_ok=True)
        baseline.write_text(json.dumps({"symbols": ["m:recorded"]}))

        update = runner.invoke(
            main, ["check", "--update-baseline"], catch_exceptions=False
        )
        assert update.exit_code == 0, update.output
        data = json.loads(baseline.read_text())
        # born-private is not enabled: its recorded set must survive.
        assert data["symbols"] == ["m:recorded"]
        assert sum(data["violations"].values()) == 1  # foo has no docstring

    def test_clear_symbol_baseline_preserves_other_namespaces(self, tmp_path):
        path = tmp_path / "check-baseline.json"
        path.write_text(
            json.dumps({"symbols": ["m:x"], "violations": {"id": 1}})
        )
        clear_symbol_baseline(path)
        assert json.loads(path.read_text()) == {"violations": {"id": 1}}
        clear_symbol_baseline(path)  # absent namespace: no-op
        clear_symbol_baseline(tmp_path / "missing.json")  # missing file: no-op
        assert json.loads(path.read_text()) == {"violations": {"id": 1}}
