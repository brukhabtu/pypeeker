"""Tests for ``check --fix`` and the first three autofixes (TASK-84).

Covers each rule's remedy end-to-end (file content asserted after apply),
the conservative decline paths (ambiguous bracket scans, decorated symbols,
files mutated between detection and plan), deterministic conflict skipping,
rollback of a check-fix transaction, the DECLARED-confidence gate, the
baseline flag conflicts, and the --update-baseline symbols-namespace
re-seed (born-private interplay).

Since TASK-124 a rule attaches an :class:`~pypeeker.intents.Intent` as
``Violation.remedy`` instead of a ``Fix`` object, and the repair runs through
that intent's registered planner. These tests therefore drive the remedy the
way ``check --fix`` does — :func:`~pypeeker.app.submit.submit_intent` — which
keeps them end-to-end over the rule/intent/planner wiring; the planners'
own scenario matrices live in ``tests/test_planner_ports.py``.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from pypeeker.app.submit import SubmitError, submit_intent
from pypeeker.check.baseline import clear_symbol_baseline
from pypeeker.check.builtin.unused_imports import _unused_imports as unused_imports
from pypeeker.check.context import CheckContext
from pypeeker.check.rules import prefer_tuple, unused_public_symbol
from pypeeker.cli import main
from pypeeker.intents import DeleteSymbolIntent, RemoveImportIntent, TuplifyIntent
from pypeeker.models.transaction import TransactionHeader
from pypeeker.refactor.applier import TransactionApplier
from pypeeker.refactor.registry import Materialized
from pypeeker.storage import TransactionStore


def _plan(store, remedy) -> Materialized:
    """Materialize a violation's remedy the way ``check --fix`` does.

    The remedy's planner persists the transaction it plans; that goes to a
    throwaway store here (as in ``app.check_fixes``) so only the edits, not
    the bookkeeping, reach these assertions.
    """
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


class TestPreferTupleRemedy:
    def test_rule_attaches_remedy(self, indexed_project):
        _, store = indexed_project(
            {"mod.py": "def f():\n    xs = [1, 2]\n    return xs[0]\n"}
        )
        [violation] = prefer_tuple(store.load("mod.py"), {})
        assert isinstance(violation.remedy, TuplifyIntent)
        assert violation.remedy.intent_id == "prefer-tuple:tuplify:mod:f:xs"

    def test_multi_element_rewrite_end_to_end(self, indexed_project):
        project_dir, store = indexed_project(
            {"mod.py": "def f():\n    xs = [1, 2, 3]\n    return xs[0]\n"}
        )
        [violation] = prefer_tuple(store.load("mod.py"), {})
        plan = _plan(store, violation.remedy)

        _apply_plan(project_dir, store, plan)
        assert (
            project_dir / "mod.py"
        ).read_text() == "def f():\n    xs = (1, 2, 3)\n    return xs[0]\n"

    def test_single_element_list_gets_trailing_comma(self, indexed_project):
        project_dir, store = indexed_project(
            {"mod.py": "def f(x):\n    xs = [x]\n    return xs[0]\n"}
        )
        [violation] = prefer_tuple(store.load("mod.py"), {})
        plan = _plan(store, violation.remedy)

        _apply_plan(project_dir, store, plan)
        # (x) would just be x — the closing bracket must become ",)".
        assert (
            project_dir / "mod.py"
        ).read_text() == "def f(x):\n    xs = (x,)\n    return xs[0]\n"

    def test_comprehension_rewritten_as_tuple_call(self, indexed_project):
        # A comprehension has no bracket-swap tuple form: [a for a in xs] must
        # become tuple(a for a in xs), never the broken (a for a in xs,)
        # (a 1-tuple wrapping a generator / SyntaxError). Regression: TASK-110.
        project_dir, store = indexed_project(
            {"mod.py": "def f(xs):\n    ys = [a for a in xs]\n    return ys[0]\n"}
        )
        [violation] = prefer_tuple(store.load("mod.py"), {})
        plan = _plan(store, violation.remedy)

        _apply_plan(project_dir, store, plan)
        assert (
            project_dir / "mod.py"
        ).read_text() == "def f(xs):\n    ys = tuple(a for a in xs)\n    return ys[0]\n"

    def test_comprehension_with_call_and_condition(self, indexed_project):
        # 'format' contains "for" but is not the loop keyword (word-boundary
        # check); the nested call parens must not be miscounted as top-level.
        src = "def f(xs):\n    ys = [format(a) for a in xs if a]\n    return ys[0]\n"
        project_dir, store = indexed_project({"mod.py": src})
        [violation] = prefer_tuple(store.load("mod.py"), {})
        plan = _plan(store, violation.remedy)

        _apply_plan(project_dir, store, plan)
        assert (project_dir / "mod.py").read_text() == (
            "def f(xs):\n    ys = tuple(format(a) for a in xs if a)\n    return ys[0]\n"
        )

    def test_single_tuple_element_keeps_trailing_comma(self, indexed_project):
        # The one element is itself a tuple; the comma inside (a, b) is nested,
        # so the list stays single-element -> ((a, b),), not ((a, b)).
        src = "def f(a, b):\n    xs = [(a, b)]\n    return xs[0]\n"
        project_dir, store = indexed_project({"mod.py": src})
        [violation] = prefer_tuple(store.load("mod.py"), {})
        plan = _plan(store, violation.remedy)

        _apply_plan(project_dir, store, plan)
        assert (project_dir / "mod.py").read_text() == (
            "def f(a, b):\n    xs = ((a, b),)\n    return xs[0]\n"
        )

    def test_multiline_literal_with_strings_and_nesting(self, indexed_project):
        source = (
            "def f():\n"
            "    xs = [\n"
            "        'a[b]',  # bracket inside a string and a comment ]\n"
            "        [1, 2],\n"
            "    ]\n"
            "    return xs[0]\n"
        )
        project_dir, store = indexed_project({"mod.py": source})
        [violation] = prefer_tuple(store.load("mod.py"), {})
        plan = _plan(store, violation.remedy)

        _apply_plan(project_dir, store, plan)
        assert (project_dir / "mod.py").read_text() == source.replace(
            "xs = [", "xs = ("
        ).replace("    ]\n    return", "    )\n    return")

    def test_fstring_in_literal_declines_ambiguous(self, indexed_project):
        _, store = indexed_project(
            {"mod.py": 'def f(x):\n    xs = [f"{x}", 1]\n    return xs[0]\n'}
        )
        [violation] = prefer_tuple(store.load("mod.py"), {})
        declined = _decline(store, violation.remedy)
        assert declined.code == 'ambiguous'
        assert "f-string" in declined.detail

    def test_mutated_file_between_detect_and_plan_declines_stale(
        self, indexed_project
    ):
        project_dir, store = indexed_project(
            {"mod.py": "def f():\n    xs = [1, 2]\n    return xs[0]\n"}
        )
        [violation] = prefer_tuple(store.load("mod.py"), {})
        # Mutate the file WITHOUT re-indexing: the index no longer
        # describes the bytes on disk, so re-locating through it is unsafe.
        (project_dir / "mod.py").write_text(
            "# moved\ndef f():\n    xs = [1, 2]\n    return xs[0]\n"
        )

        declined = _decline(store, violation.remedy)
        assert declined.code == 'stale-index'

    def test_missing_file_declines(self, indexed_project):
        project_dir, store = indexed_project(
            {"mod.py": "def f():\n    xs = [1]\n    return xs[0]\n"}
        )
        [violation] = prefer_tuple(store.load("mod.py"), {})
        (project_dir / "mod.py").unlink()

        declined = _decline(store, violation.remedy)
        assert declined.code == 'file-missing'

    def test_escaping_list_is_never_flagged_or_fixed(self, indexed_project):
        # THE safety property: in one module, a genuinely-local list is
        # tuplified while an escaping one (returned) and a mutated one (passed
        # to a function that mutates it) are left untouched — so `check --fix`
        # can never change observable behavior.
        project_dir, store = indexed_project(
            {
                "mod.py": (
                    "import heapq\n"
                    "\n"
                    "def local():\n"
                    "    xs = [3, 1, 2]\n"
                    "    for v in xs:\n"
                    "        print(v)\n"
                    "\n"
                    "def returned():\n"
                    "    ys = [4, 5]\n"
                    "    return ys\n"
                    "\n"
                    "def mutated_by_callee():\n"
                    "    zs = [0]\n"
                    "    heapq.heappush(zs, 1)\n"
                    "    return zs[0]\n"
                )
            }
        )
        flagged = {v.message for v in prefer_tuple(store.load("mod.py"), {})}
        assert any("'xs'" in m for m in flagged)
        assert not any("'ys'" in m for m in flagged)
        assert not any("'zs'" in m for m in flagged)

        for i, violation in enumerate(prefer_tuple(store.load("mod.py"), {})):
            plan = _plan(store, violation.remedy)
            _apply_plan(project_dir, store, plan, tx_id=f"fix-{i}")

        text = (project_dir / "mod.py").read_text()
        assert "xs = (3, 1, 2)" in text  # local list -> tuple
        assert "ys = [4, 5]" in text  # returned list untouched
        assert "zs = [0]" in text  # heappush target untouched


# ---------------------------------------------------------------------------
# unused-imports rule + remedy
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
        assert all(isinstance(v.remedy, RemoveImportIntent) for v in violations)

    def test_forward_ref_string_annotation_not_flagged(self, indexed_project):
        # 'Node' is used only inside a forward-ref string annotation, which the
        # binder does not descend into; it must not read as unused. 'Unused' is
        # genuinely dead and must still be flagged. Regression: TASK-111.
        _, store = indexed_project({
            "mod.py": (
                "from m import Node, Unused\n"
                "\n"
                'def f(x: "Node | None"):\n'
                "    return x\n"
            )
        })
        messages = [v.message for v in unused_imports(store.load("mod.py"), {})]
        assert messages == ["import 'Unused' is unused in this module"]

    def test_nested_forward_ref_annotation_not_flagged(self, indexed_project):
        _, store = indexed_project(
            {"mod.py": 'from m import Foo\nxs: list["Foo"] = []\n'}
        )
        assert unused_imports(store.load("mod.py"), {}) == []

    def test_dotted_side_effect_import_not_flagged(self, indexed_project):
        # ``import a.b.c`` binds a namespace and is commonly a side-effect
        # import (e.g. rule registration); its uses do not bind back to the
        # dotted symbol, so it must not be reported unused.
        _, store = indexed_project({"mod.py": "import pkg.sub.plugin\n"})
        assert unused_imports(store.load("mod.py"), {}) == []

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

    def test_skips_synthetic_dynamic_import_symbols(self, indexed_project):
        # importlib.import_module("...") is recovered by the binder as a
        # synthetic IMPORT symbol for boundary enforcement. It binds no name,
        # so it must never be reported as an unused import (and there is no
        # import statement a fix could remove).
        _, store = indexed_project({
            "mod.py": (
                "import importlib\n"
                "\n"
                "def load():\n"
                "    return importlib.import_module('os.path')\n"
            )
        })
        assert unused_imports(store.load("mod.py"), {}) == []

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
        plan = _plan(store, violation.remedy)

        _apply_plan(project_dir, store, plan)
        assert (project_dir / "mod.py").read_text() == "\ndef f():\n    return 1\n"

    def test_multi_name_first_entry_removed(self, indexed_project):
        project_dir, store = indexed_project({
            "mod.py": "import os, sys\n\ndef f():\n    return sys.argv\n"
        })
        [violation] = unused_imports(store.load("mod.py"), {})
        plan = _plan(store, violation.remedy)

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
        plan = _plan(store, violation.remedy)

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

        declined = _decline(store, violation.remedy)
        assert declined.code == 'ambiguous'

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
        # statement line the planner can edit safely: conservative decline.
        assert _decline(store, violation.remedy).code == "text-mismatch"


# ---------------------------------------------------------------------------
# unused-public-symbol --also-private deletion remedy
# ---------------------------------------------------------------------------


class TestDeleteUnusedSymbolRemedy:
    def _violations(self, store, options=None):
        if options is None:
            options = {"also-private": True}
        indexes = [store.load(p) for p in store.list_indexed_files()]
        context = CheckContext(store, indexes)
        return unused_public_symbol(context, options)

    def test_private_finding_carries_remedy_public_does_not(self, indexed_project):
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
        assert by_name["mod:visible"].remedy is None
        assert isinstance(by_name["mod:_dead"].remedy, DeleteSymbolIntent)

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
        plan = _plan(store, violation.remedy)

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
        plan = _plan(store, violation.remedy)

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

        declined = _decline(store, violation.remedy)
        assert declined.code == 'ambiguous'
        assert "decorated" in declined.detail

    def test_mutated_file_declines_stale(self, indexed_project):
        project_dir, store = indexed_project({
            "mod.py": "def _dead():\n    return 1\n"
        })
        violations = self._violations(store)
        [violation] = violations
        (project_dir / "mod.py").write_text("# shifted\ndef _dead():\n    return 1\n")

        declined = _decline(store, violation.remedy)
        assert declined.code == 'stale-index'


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
    "    return xs[0]\n"
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
    "    return xs[0]\n"
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

        assert [a["fix_id"] for a in report["fixes"]] == [
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
        # Same grammar as every other mutating command: a BOOLEAN "applied"
        # (never the fix list — see 'fixes' above), plus the apply's own
        # index bookkeeping.
        assert report["applied"] is True
        assert report["files_modified"] == ["src/mod.py"]
        assert report["files_reindex_failed"] == []

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
            {"mod.py": "def _dead():\n    xs = [1, 2]\n    return xs[0]\n"},
            rules=ALL_FIX_RULES, extra=ALSO_PRIVATE,
        )

        result = runner.invoke(main, ["check", "--fix"], catch_exceptions=False)
        report = json.loads(result.output)

        # The deletion starts earlier in the file, so it wins; the tuple
        # rewrite targets bytes inside the deleted range and is skipped.
        assert [a["fix_id"] for a in report["fixes"]] == [
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

        assert report["fixes"] == []
        assert report["tx_id"] is None
        # The heuristic finding still exists; it just never auto-fixes.
        assert report["residual_violations"] == 1
        assert "import os" in (project / "src" / "mod.py").read_text()

    def test_declined_fixes_are_reported(self, tmp_path):
        runner = CliRunner()
        _fix_project(
            tmp_path, runner,
            {"mod.py": 'def f(x):\n    xs = [f"{x}"]\n    return xs[0]\n'},
            rules='["prefer-tuple"]',
        )

        result = runner.invoke(main, ["check", "--fix"], catch_exceptions=False)
        report = json.loads(result.output)

        assert report["fixes"] == []
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
        # Nothing to fix means no transaction and therefore no apply, so
        # there is no "applied" key at all — the empty fix list lives under
        # "fixes", where a driver branching on a boolean "applied" cannot
        # mistake it for a successful mutation.
        assert report == {
            "fixes": [],
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


NON_UTF8_IMPORT = b"import os  # caf\xe9\nimport sys\n\nprint(sys.version)\n"


class TestCheckFixNonUtf8ImportLine:
    """TASK-139: a non-UTF-8 import line refuses structurally, not a crash.

    ``RemoveImportPlanner.plan`` used to ``.decode("utf-8")`` the physical
    import line raw; an undecodable byte on that line crashed ``check --fix``
    with an uncaught ``UnicodeDecodeError`` before any JSON report was ever
    written. These pin the structured refusal (mirrors TASK-136's
    extract-variable UTF-8 guard) and that an ASCII entry on a line whose
    *other* bytes are undecodable is still removable — see
    ``imports_ops._decoded_span``'s span-scoping.
    """

    def test_non_utf8_import_line_declines_through_the_standard_report(
        self, tmp_path
    ):
        runner = CliRunner()
        project = _fix_project(
            tmp_path, runner,
            {"mod.py": "import os  # note\nimport sys\n\nprint(sys.version)\n"},
            rules='["unused-imports"]',
        )
        (project / "src" / "mod.py").write_bytes(NON_UTF8_IMPORT)
        reindex = runner.invoke(
            main, ["index", str(project / "src")], catch_exceptions=False
        )
        assert reindex.exit_code == 0, reindex.output

        result = runner.invoke(main, ["check", "--fix"], catch_exceptions=False)
        report = json.loads(result.output)

        assert report["fixes"] == []
        assert report["tx_id"] is None
        [declined] = report["declined"]
        assert declined["fix_id"] == "unused-imports:remove:mod:os"
        assert declined["reason"] == "plan-refused"
        assert declined["detail"] == (
            "File is not valid UTF-8: src/mod.py "
            "(byte 16: invalid continuation byte)"
        )
        assert report["residual_violations"] == 1
        assert result.exit_code == 1
        assert (project / "src" / "mod.py").read_bytes() == NON_UTF8_IMPORT

    def test_ascii_entry_on_a_non_utf8_line_still_fixes(self, tmp_path):
        runner = CliRunner()
        project = _fix_project(
            tmp_path, runner,
            {"mod.py": "import os, sys  # note\n\nprint(sys.version)\n"},
            rules='["unused-imports"]',
        )
        (project / "src" / "mod.py").write_bytes(
            b"import os, sys  # caf\xe9\n\nprint(sys.version)\n"
        )
        reindex = runner.invoke(
            main, ["index", str(project / "src")], catch_exceptions=False
        )
        assert reindex.exit_code == 0, reindex.output

        result = runner.invoke(main, ["check", "--fix"], catch_exceptions=False)
        report = json.loads(result.output)

        assert [f["fix_id"] for f in report["fixes"]] == [
            "unused-imports:remove:mod:os"
        ]
        assert report["declined"] == []
        assert result.exit_code == 0
        assert (project / "src" / "mod.py").read_bytes() == (
            b"import sys  # caf\xe9\n\nprint(sys.version)\n"
        )


class TestCheckFixPlan:
    """``check --fix --plan``: the plan half of the uniform grammar.

    ``check --fix`` rewrites more files at once than any other mutating
    command, so it is the last one that should be unpreviewable; TASK-126
    gives it the same ``--plan`` opt-out everything else has.
    """

    def test_plan_writes_a_pending_transaction_and_touches_no_file(self, tmp_path):
        runner = CliRunner()
        project = _fix_project(
            tmp_path, runner, {"mod.py": COMBINED_SOURCE},
            rules=ALL_FIX_RULES, extra=ALSO_PRIVATE,
        )

        result = runner.invoke(main, ["check", "--fix", "--plan"])
        report = json.loads(result.output)

        # The same repairs are planned, in the same order...
        assert [a["fix_id"] for a in report["fixes"]] == [
            "unused-imports:remove:mod:os",
            "unused-imports:remove:mod:Optional",
            "prefer-tuple:tuplify:mod:use:xs",
            "unused-symbol:delete:mod:_dead",
        ]
        # ...but nothing was applied: no "applied" key, source untouched.
        assert "applied" not in report
        assert "files_reindex_failed" not in report
        assert (project / "src" / "mod.py").read_text() == COMBINED_SOURCE
        # Residual is the unfixed input set, so the exit code still reports
        # that violations are outstanding.
        assert result.exit_code == 1

        # ONE inspectable PENDING check-fix transaction holding every edit.
        tx = runner.invoke(
            main, ["transactions", "show", report["tx_id"]], catch_exceptions=False
        )
        shown = json.loads(tx.output)
        assert shown["header"]["operation"] == "check-fix"
        assert shown["header"]["status"] == "pending"

    def test_planned_fixes_apply_later_exactly_like_the_default_path(self, tmp_path):
        runner = CliRunner()
        project = _fix_project(
            tmp_path, runner, {"mod.py": COMBINED_SOURCE},
            rules=ALL_FIX_RULES, extra=ALSO_PRIVATE,
        )

        planned = runner.invoke(main, ["check", "--fix", "--plan"])
        tx_id = json.loads(planned.output)["tx_id"]

        applied = runner.invoke(main, ["apply", tx_id], catch_exceptions=False)
        assert applied.exit_code == 0, applied.output
        assert json.loads(applied.output)["status"] == "applied"
        assert (project / "src" / "mod.py").read_text() == COMBINED_FIXED

    def test_plan_without_fix_is_a_usage_error(self, tmp_path):
        runner = CliRunner()
        _fix_project(
            tmp_path, runner, {"mod.py": "x = 1\n"}, rules='["unused-imports"]'
        )
        result = runner.invoke(main, ["check", "--plan"])
        assert result.exit_code != 0
        assert "--plan only applies to --fix" in result.output


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
        baseline = project / ".pypeeker" / "check-baseline.json"

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
        baseline = project / ".pypeeker" / "check-baseline.json"
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
