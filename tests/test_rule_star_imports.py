"""Tests for the star-imports rule and RewriteStarImportFix (TASK-90).

Covers the binder fact (a ``"*"`` IMPORT symbol per star import, relative
imports resolved, shadow-suffixed ids, name resolution untouched), the
project rule's used-name attribution (single star DECLARED with a fix,
multi-star HEURISTIC without one, unindexed targets, ``__all__`` v1
behavior), the fix's rewrite and decline paths, and the two end-to-end
routes: ``check --fix`` and a plan-batch fix sweep including a module that
uses names from two star imports.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from click.testing import CliRunner

from pypeeker.check.builtin.star_imports import (
    STAR_IMPORTS,
    RewriteStarImportFix,
    star_imports,
)
from pypeeker.check.context import CheckContext
from pypeeker.check.fixes import DeclineReason, FixDeclined, FixPlan
from pypeeker.check.rules import get_project_rule
from pypeeker.cli import main
from pypeeker.models.capabilities import Confidence
from pypeeker.models.symbols import SymbolKind
from pypeeker.models.transaction import TransactionHeader
from pypeeker.refactor.applier import TransactionApplier
from pypeeker.storage import IndexStore, TransactionStore

LIB = "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n\n\n_hidden = 3\n"
APP = "from lib import *\n\n\ndef use():\n    return beta() + alpha()\n"


def _run_rule(store) -> list:
    """Run the star-imports rule over every index in ``store``."""
    indexes = [store.load(p) for p in store.list_indexed_files()]
    return star_imports(CheckContext(store, indexes), {})


def _apply_plan(project_dir: Path, store, plan: FixPlan) -> None:
    """Apply a FixPlan through the standard transaction machinery."""
    tx_store = TransactionStore(project_dir)
    header = TransactionHeader(
        tx_id="star-fix-tx",
        symbol_id="",
        old_name="",
        new_name="",
        created_at="2026-06-11T00:00:00+00:00",
        operation="check-fix",
    )
    tx_store.save(header, plan.edits)
    result = TransactionApplier(store, tx_store).apply("star-fix-tx")
    assert result["status"] == "applied"


# ---------------------------------------------------------------------------
# binder fact
# ---------------------------------------------------------------------------


class TestBinderStarImportFact:
    def test_star_import_recorded_as_import_symbol(self, bind_source):
        index = bind_source("from os.path import *\nx = join('a')\n", "m.py")
        [star] = [s for s in index.symbols if s.name == "*"]
        assert star.kind is SymbolKind.IMPORT
        assert star.symbol_id == "m:*"
        # imported_from names the module itself, not a module.name path.
        assert star.imported_from == "os.path"
        # The location is the ``*`` token (line 0, after "from os.path import ").
        assert star.location.span.start.line == 0
        assert star.location.span.start.column == 20

    def test_star_supplied_names_stay_unresolved(self, bind_source):
        index = bind_source("from os.path import *\nx = join('a')\n", "m.py")
        [ref] = [r for r in index.references if r.symbol_id == "join"]
        assert ref.resolved is False

    def test_relative_star_import_resolves_module(self, bind_source):
        index = bind_source("from .sibling import *\n", "pkg/mod.py")
        [star] = [s for s in index.symbols if s.name == "*"]
        assert star.imported_from == "pkg.sibling"

    def test_two_star_imports_get_shadow_suffixed_ids(self, bind_source):
        index = bind_source("from a import *\nfrom b import *\n", "m.py")
        stars = [s for s in index.symbols if s.name == "*"]
        assert [s.symbol_id for s in stars] == ["m:*", "m:*$2"]
        assert [s.imported_from for s in stars] == ["a", "b"]

    def test_name_resolution_unaffected(self, bind_source):
        # Declaring "*" must not perturb normal lookup: an explicitly
        # imported name on the same line shape still resolves.
        index = bind_source(
            "from os.path import join\nfrom glob import *\ny = join('a')\n",
            "m.py",
        )
        [ref] = [r for r in index.references if r.symbol_id == "m:join"]
        assert ref.resolved is True


# ---------------------------------------------------------------------------
# rule detection
# ---------------------------------------------------------------------------


class TestStarImportsRule:
    def test_single_star_reports_sorted_used_names_with_fix(
        self, indexed_project
    ):
        _, store = indexed_project({"lib.py": LIB, "app.py": APP})
        [violation] = _run_rule(store)
        assert violation.rule == STAR_IMPORTS
        assert violation.file_path == "app.py"
        assert violation.line == 1
        assert violation.message == (
            "star import from 'lib' — 2 names actually used: alpha, beta"
        )
        assert violation.confidence is Confidence.DECLARED
        assert isinstance(violation.fix, RewriteStarImportFix)
        assert violation.fix.fix_id == "star-imports:rewrite:app:*"

    def test_private_target_names_are_not_attributed(self, indexed_project):
        # _hidden is in lib but never part of a star's public surface.
        _, store = indexed_project({
            "lib.py": LIB,
            "app.py": "from lib import *\n\nx = alpha()\n",
        })
        [violation] = _run_rule(store)
        assert "1 name actually used: alpha" in violation.message

    def test_zero_used_names_suggests_deletion_without_fix(
        self, indexed_project
    ):
        _, store = indexed_project({
            "lib.py": LIB,
            "app.py": "from lib import *\n\nx = 1\n",
        })
        [violation] = _run_rule(store)
        assert violation.message == (
            "star import from 'lib' — 0 names actually used; "
            "consider deleting the import"
        )
        assert violation.confidence is Confidence.DECLARED
        assert violation.fix is None

    def test_unindexed_target_is_heuristic_without_fix(self, indexed_project):
        _, store = indexed_project({
            "app.py": "from os.path import *\n\nx = join('a')\n",
        })
        [violation] = _run_rule(store)
        assert violation.message == (
            "star import from 'os.path' — target module is not indexed; "
            "used names unknown"
        )
        assert violation.confidence is Confidence.HEURISTIC
        assert violation.fix is None

    def test_multi_star_first_wins_heuristic_no_fix(self, indexed_project):
        # ``shared`` is defined by BOTH targets; first-star-wins attributes
        # it to liba even though Python's runtime last-wins would pick libb.
        # That deliberate simplification is why both findings are HEURISTIC
        # and carry no fix.
        _, store = indexed_project({
            "liba.py": "def shared():\n    return 1\n\n\ndef only_a():\n    return 2\n",
            "libb.py": "def shared():\n    return 3\n\n\ndef only_b():\n    return 4\n",
            "app.py": (
                "from liba import *\n"
                "from libb import *\n"
                "\n"
                "x = shared() + only_a() + only_b()\n"
            ),
        })
        violations = _run_rule(store)
        assert [v.message for v in violations] == [
            "star import from 'liba' — 2 names actually used: only_a, shared",
            "star import from 'libb' — 1 name actually used: only_b",
        ]
        assert all(v.confidence is Confidence.HEURISTIC for v in violations)
        assert all(v.fix is None for v in violations)

    def test_target_with_dunder_all_matches_public_surface(
        self, indexed_project
    ):
        # __all__ contents are not parseable from the index (it is recorded
        # only as a VARIABLE), so v1 matches public symbols regardless —
        # and never attributes __all__ itself (underscore-prefixed).
        _, store = indexed_project({
            "lib.py": "__all__ = ['alpha']\n\n\ndef alpha():\n    return 1\n\n\ndef beta():\n    return 2\n",
            "app.py": "from lib import *\n\nx = beta()\n",
        })
        [violation] = _run_rule(store)
        assert "1 name actually used: beta" in violation.message

    def test_registered_as_project_rule(self):
        assert get_project_rule(STAR_IMPORTS) is star_imports

    def test_not_in_default_rules(self):
        # star-imports is available but opt-in.
        import tomllib

        # Resolve relative to this file: earlier CLI tests may leave the
        # process cwd inside a tmp project.
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text())
        assert STAR_IMPORTS not in data["tool"]["pypeeker"]["rules"]


# ---------------------------------------------------------------------------
# the fix
# ---------------------------------------------------------------------------


class TestRewriteStarImportFix:
    def test_rewrite_preserves_module_text_and_comment(self, indexed_project):
        project_dir, store = indexed_project({
            "pkg/__init__.py": "",
            "pkg/sib.py": "def gamma():\n    return 1\n",
            "pkg/mod.py": "from .sib import *  # keep\n\nx = gamma()\n",
        })
        [violation] = _run_rule(store)
        plan = violation.fix.plan(store)
        assert isinstance(plan, FixPlan)

        _apply_plan(project_dir, store, plan)
        # Only the ``*`` is replaced: the relative module spelling and the
        # trailing comment survive.
        assert (project_dir / "pkg" / "mod.py").read_text() == (
            "from .sib import gamma  # keep\n\nx = gamma()\n"
        )

    def test_zero_used_names_declines_with_deletion_advice(
        self, indexed_project
    ):
        _, store = indexed_project({
            "lib.py": LIB,
            "app.py": "from lib import *\n\nx = 1\n",
        })
        fix = RewriteStarImportFix(
            file_path="app.py", symbol_id="app:*", module="lib"
        )
        declined = fix.plan(store)
        assert isinstance(declined, FixDeclined)
        assert declined.reason is DeclineReason.AMBIGUOUS
        assert "delete the star import" in declined.detail

    def test_unattributable_name_declines(self, indexed_project):
        # ``ghost`` matches no indexed module's surface: the star might
        # still supply it (lib could star-import an unindexed module), so
        # the rewrite cannot be proven complete.
        _, store = indexed_project({
            "lib.py": LIB,
            "app.py": "from lib import *\n\nx = alpha() + ghost()\n",
        })
        [violation] = _run_rule(store)
        assert isinstance(violation.fix, RewriteStarImportFix)

        declined = violation.fix.plan(store)
        assert isinstance(declined, FixDeclined)
        assert declined.reason is DeclineReason.AMBIGUOUS
        assert "'ghost'" in declined.detail
        assert "may still supply" in declined.detail

    def test_multi_star_file_declines_at_plan_time(self, indexed_project):
        _, store = indexed_project({
            "liba.py": "def only_a():\n    return 1\n",
            "libb.py": "def only_b():\n    return 2\n",
            "app.py": (
                "from liba import *\nfrom libb import *\n\nx = only_a()\n"
            ),
        })
        fix = RewriteStarImportFix(
            file_path="app.py", symbol_id="app:*", module="liba"
        )
        declined = fix.plan(store)
        assert isinstance(declined, FixDeclined)
        assert declined.reason is DeclineReason.AMBIGUOUS
        assert "star imports" in declined.detail

    def test_unindexed_target_declines(self, indexed_project):
        _, store = indexed_project({
            "app.py": "from os.path import *\n\nx = join('a')\n",
        })
        fix = RewriteStarImportFix(
            file_path="app.py", symbol_id="app:*", module="os.path"
        )
        declined = fix.plan(store)
        assert isinstance(declined, FixDeclined)
        assert declined.reason is DeclineReason.AMBIGUOUS
        assert "not indexed" in declined.detail

    def test_mutated_file_declines_stale_index(self, indexed_project):
        project_dir, store = indexed_project({"lib.py": LIB, "app.py": APP})
        [violation] = _run_rule(store)
        # Mutate WITHOUT re-indexing: the index no longer describes the
        # bytes on disk, so re-deriving through it is unsafe.
        (project_dir / "app.py").write_text("# shifted\n" + APP)

        declined = violation.fix.plan(store)
        assert isinstance(declined, FixDeclined)
        assert declined.reason is DeclineReason.STALE_INDEX

    def test_missing_file_declines(self, indexed_project):
        project_dir, store = indexed_project({"lib.py": LIB, "app.py": APP})
        [violation] = _run_rule(store)
        (project_dir / "app.py").unlink()

        declined = violation.fix.plan(store)
        assert isinstance(declined, FixDeclined)
        assert declined.reason is DeclineReason.FILE_MISSING


# ---------------------------------------------------------------------------
# end-to-end: check --fix and plan-batch
# ---------------------------------------------------------------------------

PYPROJECT = (
    '[project]\nname = "test"\n'
    "[tool.pypeeker]\n"
    'src = ["src"]\n'
    'rules = ["star-imports"]\n'
)


def _cli_project(tmp_path: Path, runner: CliRunner, files: dict[str, str]) -> Path:
    """A cwd'd tmp project with ``files`` under src/, indexed via the CLI."""
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    for name, content in files.items():
        path = tmp_path / "src" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    os.chdir(tmp_path)
    result = runner.invoke(
        main, ["index", str(tmp_path / "src")], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    return tmp_path


class TestStarImportsEndToEnd:
    def test_check_fix_rewrites_star_and_refs_resolve_on_reindex(
        self, tmp_path
    ):
        runner = CliRunner()
        project = _cli_project(tmp_path, runner, {"lib.py": LIB, "app.py": APP})

        result = runner.invoke(main, ["check", "--fix"], catch_exceptions=False)
        report = json.loads(result.output)

        assert [a["fix_id"] for a in report["applied"]] == [
            "star-imports:rewrite:app:*"
        ]
        assert report["declined"] == []
        assert report["residual_violations"] == 0
        assert result.exit_code == 0, result.output
        assert (project / "src" / "app.py").read_text() == (
            "from lib import alpha, beta\n\n\ndef use():\n"
            "    return beta() + alpha()\n"
        )
        # check --fix re-indexed the edited file: the previously-unresolved
        # star-supplied names now bind to the explicit import symbols.
        index = IndexStore(project).load("src/app.py")
        names = {s.name for s in index.symbols if s.kind is SymbolKind.IMPORT}
        assert names == {"alpha", "beta"}
        for name in ("alpha", "beta"):
            refs = [r for r in index.references if r.symbol_id == f"app:{name}"]
            assert refs and all(r.resolved for r in refs)

    def test_plan_batch_fix_sweep_skips_two_star_module(self, tmp_path):
        # AC #3: end-to-end through plan-batch, including a module that uses
        # names from TWO star imports — its findings are HEURISTIC, so the
        # sweep expands no fix for it and the file stays untouched, while
        # the single-star module is rewritten.
        runner = CliRunner()
        multi = (
            "from liba import *\n"
            "from libb import *\n"
            "\n"
            "y = only_a() + only_b()\n"
        )
        project = _cli_project(
            tmp_path,
            runner,
            {
                "liba.py": "def only_a():\n    return 1\n",
                "libb.py": "def only_b():\n    return 2\n",
                "single.py": "from liba import *\n\nz = only_a()\n",
                "multi.py": multi,
            },
        )
        intents = tmp_path / "intents.json"
        intents.write_text(
            json.dumps([{"kind": "fix", "id": "destar", "rule": STAR_IMPORTS}])
        )

        result = runner.invoke(
            main, ["plan-batch", str(intents)], catch_exceptions=False
        )
        output = json.loads(result.output)
        assert result.exit_code == 0, output
        assert output["dropped"] == []
        # Exactly one fix intent: the single-star module's rewrite.
        assert [e["id"] for e in output["executed"]] == ["destar-1"]
        assert output["files_affected"] == ["src/single.py"]

        applied = runner.invoke(
            main, ["apply", output["tx_id"]], catch_exceptions=False
        )
        assert json.loads(applied.output)["status"] == "applied"
        assert (project / "src" / "single.py").read_text() == (
            "from liba import only_a\n\nz = only_a()\n"
        )
        assert (project / "src" / "multi.py").read_text() == multi
