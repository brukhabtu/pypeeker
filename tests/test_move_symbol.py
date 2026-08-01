"""move-symbol: the payoff refactoring of TASK-131 (Plan D PR3).

Four layers, in the order a move passes through them:

* :class:`~pypeeker.intents.anchors.EdgeAnchor` — the third anchor shape, its
  remap semantics, and the fact that the move planner is what consumes it;
* :class:`~pypeeker.intents.MoveSymbolIntent` — the footprint's
  *unconditional* destination (the property that keeps the batch schedule
  independent of the filesystem) and the effect's existence-gated
  ``files_created``;
* :class:`~pypeeker.refactor.move.MoveSymbolPlanner` through the CLI — a move
  into a module that does not exist, a move into one that does, the importer
  matrix, and the refusal matrix keyed by precondition name;
* the batch engine — a move flattened into one transaction alongside another
  intent, applied and rolled back.

Every applied case is verified by *bytes on disk* and then by ``rollback``
restoring them, because a move is the first refactoring whose inverse has to
undo a file's existence and not only its contents.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import get_args

import pytest
from click.testing import CliRunner

from pypeeker.app import SubmitError, submit_intent
from pypeeker.cli import main
from pypeeker.intents import (
    IMPORT_EDGE,
    Anchor,
    EdgeAnchor,
    Effect,
    MoveSymbolIntent,
    OrphanedIntent,
    OrphanReason,
    RenameIntent,
    SymbolAnchor,
    module_file_path,
)
from pypeeker.refactor.batch import BatchPolicy, flatten_batch, run_batch
from pypeeker.refactor.move import MoveSymbolError, MoveSymbolPlanner
from pypeeker.storage import TransactionStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project(
    tmp_path: Path, files: dict[str, str], *, src_roots: str = ""
) -> Path:
    """A project directory with source files, a pyproject and an index dir."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "test"\n' + src_roots
    )
    (tmp_path / ".pypeeker" / "index").mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return tmp_path


def _cli(project: Path, args: list[str]) -> tuple[int, dict]:
    """Run one CLI command inside ``project``, returning (exit code, JSON)."""
    os.chdir(project)
    result = CliRunner().invoke(main, args, catch_exceptions=False)
    return result.exit_code, json.loads(result.output)


def _indexed(tmp_path: Path, files: dict[str, str], *, src_roots: str = "") -> Path:
    """A project whose sources are indexed through the real ``index`` command."""
    project = _project(tmp_path, files, src_roots=src_roots)
    code, _ = _cli(project, ["index", str(project)])
    assert code == 0
    return project


def _snapshot(project: Path) -> dict[str, str]:
    """Every ``.py`` file's text, keyed by project-relative path."""
    return {
        str(path.relative_to(project)): path.read_text()
        for path in sorted(project.rglob("*.py"))
        if ".pypeeker" not in path.parts
    }


_LIB = {
    "pkg/__init__.py": '"""pkg."""\n',
    "pkg/lib.py": '"""lib."""\n\n\ndef helper(value):\n    """Help."""\n    return value + 1\n',
    "app.py": "from pkg.lib import helper\n\n\ndef run():\n    return helper(1)\n",
}


# ---------------------------------------------------------------------------
# EdgeAnchor: the third anchor shape
# ---------------------------------------------------------------------------


class TestEdgeAnchor:
    def test_joins_the_anchor_union_and_the_barrel(self):
        assert EdgeAnchor in get_args(Anchor)
        import pypeeker.intents as barrel

        assert "EdgeAnchor" in barrel.__all__
        assert barrel.EdgeAnchor is EdgeAnchor

    def test_the_deferral_paragraph_is_deleted_not_edited(self):
        """The docstring may not merely *describe* the deferral differently.

        Plan D's acceptance criterion is explicit that the paragraph is
        deleted, so this asserts on its absence rather than on new prose:
        an ``EdgeAnchor`` that ships with a note saying it is deferred is a
        contradiction a reader would have to resolve by reading the code.
        """
        from pypeeker.intents import anchors

        assert "deliberately **not** added" not in anchors.__doc__
        assert "deferred" not in anchors.__doc__

    def test_is_frozen_and_hashable(self):
        edge = EdgeAnchor("app:helper", "pkg.lib:helper")
        assert edge.kind == IMPORT_EDGE
        assert {edge, EdgeAnchor("app:helper", "pkg.lib:helper")} == {edge}
        with pytest.raises(Exception):
            edge.source_id = "other"  # type: ignore[misc]

    def test_remap_follows_a_rename_of_either_endpoint(self):
        edge = EdgeAnchor("app:helper", "pkg.lib:helper")
        effect = Effect(renamed={"pkg.lib:helper": "pkg.lib:assist"})

        remapped = edge.remap(effect)

        assert remapped == EdgeAnchor("app:helper", "pkg.lib:assist")

    def test_remap_follows_a_move_because_a_move_is_a_rename_in_id_space(self):
        """The composition this shape exists for: a move re-homes the target."""
        edge = EdgeAnchor("app:helper", "pkg.lib:helper")
        move = MoveSymbolIntent("m", "pkg.lib:helper", "pkg.util")
        effect = Effect(renamed={"pkg.lib:helper": move.destination_id})

        assert edge.remap(effect) == EdgeAnchor("app:helper", "pkg.util:helper")

    def test_remap_descends_a_prefix_rename(self):
        edge = EdgeAnchor("app:Svc", "pkg.lib:Svc.run")
        effect = Effect(renamed={"pkg.lib:Svc": "pkg.util:Svc"})

        assert edge.remap(effect).target_id == "pkg.util:Svc.run"

    def test_remap_returns_self_when_nothing_moved(self):
        edge = EdgeAnchor("app:helper", "pkg.lib:helper")
        assert edge.remap(Effect()) is edge

    @pytest.mark.parametrize("dead", ["app:helper", "pkg.lib:helper"])
    def test_a_deleted_endpoint_orphans_the_edge(self, dead):
        """Either end going away orphans the edge — with no new enum member.

        The reported reason stays :attr:`OrphanReason.ANCHOR_DELETED`: an
        edge whose endpoint was deleted *is* a deleted anchor, and the
        machine-readable drop vocabulary in batch reports must not grow a
        value for a distinction nothing consumes.
        """
        edge = EdgeAnchor("app:helper", "pkg.lib:helper")
        effect = Effect(deleted={dead})

        assert edge.remap(effect) is None
        # The caller names the dead endpoint from the same substitution.
        dead_ends = [
            end
            for end in (edge.source_id, edge.target_id)
            if effect.remap_id(end) is None
        ]
        assert dead_ends == [dead]
        assert OrphanReason.ANCHOR_DELETED.value == "anchor-deleted"

    def test_the_move_intent_derives_one_edge_per_importer(self, tmp_path):
        project = _indexed(
            tmp_path,
            {
                **_LIB,
                "other.py": "from pkg.lib import helper as h\n\n\ndef go():\n    return h(2)\n",
            },
        )
        from pypeeker.storage import IndexStore

        store = IndexStore(project)
        edges = MoveSymbolIntent("m", "pkg.lib:helper", "pkg.util").import_edges(store)

        assert edges == (
            EdgeAnchor("app:helper", "pkg.lib:helper"),
            EdgeAnchor("other:h", "pkg.lib:helper"),
        )

    def test_the_planner_refuses_by_naming_the_edge(self, tmp_path):
        """EdgeAnchor's whole point: a refusal says *which edge*, not which file."""
        project = _indexed(
            tmp_path,
            {
                **_LIB,
                "dup.py": "from pkg.lib import helper, helper\n\n\ndef go():\n    return helper(1)\n",
            },
        )
        from pypeeker.storage import IndexStore

        with pytest.raises(MoveSymbolError) as excinfo:
            MoveSymbolPlanner(
                IndexStore(project), TransactionStore(project)
            ).plan(SymbolAnchor("pkg.lib:helper"), "pkg.util")

        assert excinfo.value.precondition == "import-edge-rewritable"
        assert "import edge 'dup:helper' -> 'pkg.lib:helper'" in str(excinfo.value)


# ---------------------------------------------------------------------------
# MoveSymbolIntent: footprint, effect, remap
# ---------------------------------------------------------------------------


class TestMoveSymbolIntent:
    def test_kind_and_description(self):
        intent = MoveSymbolIntent("m", "pkg.lib:helper", "pkg.util")
        assert intent.kind == "move-symbol"
        assert intent.anchor == SymbolAnchor("pkg.lib:helper")
        assert intent.destination_id == "pkg.util:helper"
        assert intent.description == "move 'helper' from 'pkg.lib' to 'pkg.util'"

    def test_footprint_declares_the_destination_whether_or_not_it_exists(
        self, tmp_path
    ):
        """Adjustment (a): the footprint may not depend on the filesystem.

        ``batch._order_key`` is ``sorted(writes_files | reads_files)[0]``, so
        a footprint that only mentioned an *existing* destination would make
        the batch schedule depend on whether the target file happens to be
        there — the one way filesystem state can leak into an otherwise pure
        scheduler.
        """
        from pypeeker.storage import IndexStore

        store = IndexStore(_indexed(tmp_path / "absent", dict(_LIB)))
        intent = MoveSymbolIntent("m", "pkg.lib:helper", "pkg.util")

        absent = intent.footprint(store)
        assert "pkg/util.py" in absent.writes_files
        assert "pkg/util.py" in absent.reads_files

        # Same question with the destination present *and* indexed.
        present = IndexStore(
            _indexed(tmp_path / "present", {**_LIB, "pkg/util.py": '"""util."""\n'})
        )
        assert intent.footprint(present) == absent

    def test_footprint_covers_source_importers_and_both_ids(self, indexed_project):
        _, store = indexed_project(dict(_LIB))
        footprint = MoveSymbolIntent("m", "pkg.lib:helper", "pkg.util").footprint(store)

        assert footprint.writes_symbols == {"pkg.lib:helper", "pkg.util:helper"}
        assert {"pkg/lib.py", "app.py", "pkg/util.py"} <= footprint.writes_files

    def test_effect_is_a_rename_in_id_space(self, indexed_project):
        _, store = indexed_project(dict(_LIB))
        effect = MoveSymbolIntent("m", "pkg.lib:helper", "pkg.util").predicted_effect(
            store
        )

        assert effect.renamed == (("pkg.lib:helper", "pkg.util:helper"),)
        assert effect.remap_id("pkg.lib:helper") == "pkg.util:helper"
        assert effect.deleted == frozenset()

    def test_only_files_created_is_existence_gated(self, tmp_path):
        from pypeeker.storage import IndexStore

        absent = IndexStore(_indexed(tmp_path / "absent", dict(_LIB)))
        present = IndexStore(
            _indexed(tmp_path / "present", {**_LIB, "pkg/util.py": '"""util."""\n'})
        )
        intent = MoveSymbolIntent("m", "pkg.lib:helper", "pkg.util")

        assert intent.predicted_effect(absent).files_created == {"pkg/util.py"}
        assert intent.predicted_effect(present).files_created == frozenset()

    def test_remap_follows_a_prior_rename_and_orphans_on_delete(self):
        intent = MoveSymbolIntent("m", "pkg.lib:helper", "pkg.util")

        renamed = intent.remap(Effect(renamed={"pkg.lib:helper": "pkg.lib:assist"}))
        assert renamed.symbol_id == "pkg.lib:assist"
        assert renamed.dest_module == "pkg.util"

        orphan = intent.remap(Effect(deleted={"pkg.lib:helper"}))
        assert isinstance(orphan, OrphanedIntent)
        assert orphan.reason is OrphanReason.ANCHOR_DELETED

    def test_module_file_path_inverts_the_layout_without_project_config(
        self, indexed_project
    ):
        """``intents`` may not import ``project``; the store answers instead."""
        _, store = indexed_project(dict(_LIB))

        assert module_file_path(store, "pkg.lib") == "pkg/lib.py"
        assert module_file_path(store, "pkg") == "pkg/__init__.py"
        assert module_file_path(store, "pkg.util") == "pkg/util.py"
        assert module_file_path(store, "deeply.nested.new") == "deeply/nested/new.py"

    def test_module_file_path_derives_a_configured_src_root(self, tmp_path):
        """The layout is recovered from the index, not from ``[tool.pypeeker].src``.

        ``intents`` cannot read the project config, so the prefix is derived
        by inverting the (module id, path) pairs the store already holds. A
        ``src/`` project is the case that proves it is really inverting and
        not just echoing paths.
        """
        from pypeeker.storage import IndexStore

        project = _indexed(
            tmp_path,
            {
                "src/pkg/__init__.py": '"""pkg."""\n',
                "src/pkg/lib.py": '"""lib."""\n\n\ndef f():\n    return 1\n',
            },
            src_roots='\n[tool.pypeeker]\nsrc = ["src"]\n',
        )
        store = IndexStore(project)

        assert module_file_path(store, "pkg.lib") == "src/pkg/lib.py"
        assert module_file_path(store, "pkg") == "src/pkg/__init__.py"
        assert module_file_path(store, "pkg.util") == "src/pkg/util.py"

    def test_module_file_path_follows_the_destination_ancestor_not_the_majority(
        self, tmp_path
    ):
        """The prefix comes from the package the destination joins, not a vote.

        One ``src/``-rooted package and four root-level modules make ``""``
        the *majority* prefix, so a majority-only answer puts ``app.newmod``
        at ``./app/newmod.py`` while every importer is rewritten to
        ``from app.newmod import ...`` — a module born outside the source
        root, unreachable from the import it was created for. ``app`` is
        itself indexed, so the right prefix was in the store all along.
        """
        from pypeeker.storage import IndexStore

        project = _indexed(
            tmp_path,
            {
                "src/app/__init__.py": '"""app."""\n',
                "src/app/a.py": '"""a."""\n\n\ndef helper(v):\n    return v\n',
                "src/app/c.py": "from app.a import helper\n\n\ndef run():\n    return helper(1)\n",
                "t1.py": '"""t1."""\n',
                "t2.py": '"""t2."""\n',
                "t3.py": '"""t3."""\n',
                "t4.py": '"""t4."""\n',
            },
        )
        store = IndexStore(project)

        assert module_file_path(store, "app.newmod") == "src/app/newmod.py"
        # No indexed ancestor: the majority prefix is still the fallback.
        assert module_file_path(store, "solo") == "solo.py"

    def test_a_move_whose_package_is_not_the_majority_root_still_lands_inside_it(
        self, tmp_path
    ):
        project = _indexed(
            tmp_path,
            {
                "src/app/__init__.py": '"""app."""\n',
                "src/app/a.py": '"""a."""\n\n\ndef helper(v):\n    return v\n',
                "src/app/c.py": "from app.a import helper\n\n\ndef run():\n    return helper(1)\n",
                "t1.py": '"""t1."""\n',
                "t2.py": '"""t2."""\n',
                "t3.py": '"""t3."""\n',
                "t4.py": '"""t4."""\n',
            },
        )

        code, payload = _cli(project, ["move-symbol", "app.a:helper", "app.newmod"])

        assert code == 0
        assert payload["files_created"] == ["src/app/newmod.py"]
        assert not (project / "app").exists()
        assert (project / "src" / "app" / "c.py").read_text().startswith(
            "from app.newmod import helper"
        )

    def test_a_move_in_a_src_layout_lands_under_the_src_root(self, tmp_path):
        project = _indexed(
            tmp_path,
            {
                "src/pkg/__init__.py": '"""pkg."""\n',
                "src/pkg/lib.py": '"""lib."""\n\n\ndef helper(v):\n    return v\n',
                "src/app.py": "from pkg.lib import helper\n\n\ndef run():\n    return helper(1)\n",
            },
            src_roots='\n[tool.pypeeker]\nsrc = ["src"]\n',
        )

        code, payload = _cli(project, ["move-symbol", "pkg.lib:helper", "pkg.util"])

        assert code == 0
        assert payload["files_created"] == ["src/pkg/util.py"]
        assert (project / "src" / "pkg" / "util.py").read_text().startswith(
            '"""pkg.util."""'
        )
        assert (project / "src" / "app.py").read_text().startswith(
            "from pkg.util import helper"
        )


# ---------------------------------------------------------------------------
# End to end: a destination module that does not exist yet
# ---------------------------------------------------------------------------


class TestMoveToNewModule:
    def test_creates_the_module_deletes_the_source_and_rewrites_importers(
        self, tmp_path
    ):
        project = _indexed(tmp_path, dict(_LIB))
        before = _snapshot(project)

        code, payload = _cli(project, ["move-symbol", "pkg.lib:helper", "pkg.util"])

        assert code == 0
        assert payload["operation"] == "move-symbol"
        assert payload["symbol_id"] == "pkg.lib:helper"
        assert payload["old_name"] == "pkg.lib"
        assert payload["new_name"] == "pkg.util"
        assert payload["applied"] is True
        assert payload["files_created"] == ["pkg/util.py"]
        assert set(payload["files_modified"]) == {"pkg/lib.py", "app.py"}
        assert payload["files_reindex_failed"] == []

        # The newborn module: docstring, then the definition verbatim.
        assert (project / "pkg" / "util.py").read_text() == (
            '"""pkg.util."""\n\n\ndef helper(value):\n    """Help."""\n    return value + 1\n'
        )
        # The source lost the definition and the blank lines *below* it — the
        # same span discipline delete-symbol uses, which never reaches back
        # above the definition line.
        assert (project / "pkg" / "lib.py").read_text() == '"""lib."""\n\n\n'
        # The importer names the new home.
        assert (project / "app.py").read_text() == (
            "from pkg.util import helper\n\n\ndef run():\n    return helper(1)\n"
        )

        # ONE transaction expresses the whole move.
        _, listed = _cli(project, ["transactions", "list"])
        assert [tx["tx_id"] for tx in listed] == [payload["tx_id"]]
        assert listed[0]["status"] == "applied"
        assert listed[0]["operation"] == "move-symbol"

        # ... and rolling it back restores every byte, the newborn included.
        code, rolled = _cli(project, ["rollback", payload["tx_id"]])
        assert code == 0
        assert not (project / "pkg" / "util.py").exists()
        assert _snapshot(project) == before

    def test_the_transaction_carries_a_create_entry(self, tmp_path):
        project = _indexed(tmp_path, dict(_LIB))
        _, payload = _cli(project, ["move-symbol", "pkg.lib:helper", "pkg.util", "--plan"])

        _, shown = _cli(project, ["transactions", "show", payload["tx_id"]])

        assert [entry["path"] for entry in shown["creates"]] == ["pkg/util.py"]
        assert shown["creates"][0]["content"].startswith('"""pkg.util."""')
        assert shown["deletes"] == []
        assert shown["header"]["operation"] == "move-symbol"

    def test_a_nested_destination_package_is_conjured_and_undone(self, tmp_path):
        project = _indexed(tmp_path, dict(_LIB))

        code, payload = _cli(
            project, ["move-symbol", "pkg.lib:helper", "pkg.deep.inner.util"]
        )

        assert code == 0
        assert (project / "pkg" / "deep" / "inner" / "util.py").exists()

        _cli(project, ["rollback", payload["tx_id"]])
        assert not (project / "pkg" / "deep").exists()

    def test_the_moved_body_carries_the_imports_it_uses(self, tmp_path):
        project = _indexed(
            tmp_path,
            {
                "pkg/__init__.py": '"""pkg."""\n',
                "pkg/lib.py": (
                    '"""lib."""\n'
                    "import os\n"
                    "from typing import Any as Anything\n"
                    "\n\n"
                    "def helper(value: Anything) -> str:\n"
                    "    return os.path.basename(value)\n"
                ),
                "app.py": "from pkg.lib import helper\n\n\ndef run():\n    return helper('/a')\n",
            },
        )

        code, _ = _cli(project, ["move-symbol", "pkg.lib:helper", "pkg.util"])

        assert code == 0
        assert (project / "pkg" / "util.py").read_text() == (
            '"""pkg.util."""\n'
            "\n"
            "from typing import Any as Anything\n"
            "import os\n"
            "\n\n"
            "def helper(value: Anything) -> str:\n"
            "    return os.path.basename(value)\n"
        )

    def test_plan_mode_writes_pending_and_touches_nothing(self, tmp_path):
        project = _indexed(tmp_path, dict(_LIB))
        before = _snapshot(project)

        code, payload = _cli(
            project, ["move-symbol", "pkg.lib:helper", "pkg.util", "--plan"]
        )

        assert code == 0
        assert "applied" not in payload
        assert _snapshot(project) == before
        assert not (project / "pkg" / "util.py").exists()

        _, listed = _cli(project, ["transactions", "list"])
        assert listed[0]["status"] == "pending"

        # The PENDING plan is still applicable afterwards.
        code, applied = _cli(project, ["apply", payload["tx_id"]])
        assert code == 0
        assert applied["files_created"] == ["pkg/util.py"]


# ---------------------------------------------------------------------------
# End to end: a destination module that already exists
# ---------------------------------------------------------------------------


class TestMoveToExistingModule:
    def test_appends_with_two_blank_lines_and_no_create_entry(self, tmp_path):
        project = _indexed(
            tmp_path,
            {
                **_LIB,
                "pkg/util.py": '"""util."""\n\n\ndef existing():\n    return 0\n',
            },
        )
        before = _snapshot(project)

        code, payload = _cli(project, ["move-symbol", "pkg.lib:helper", "pkg.util"])

        assert code == 0
        assert payload["files_created"] == []
        assert set(payload["files_modified"]) == {"pkg/lib.py", "pkg/util.py", "app.py"}
        assert (project / "pkg" / "util.py").read_text() == (
            '"""util."""\n'
            "\n\n"
            "def existing():\n"
            "    return 0\n"
            "\n\n"
            "def helper(value):\n"
            '    """Help."""\n'
            "    return value + 1\n"
        )

        _cli(project, ["rollback", payload["tx_id"]])
        assert _snapshot(project) == before

    def test_an_empty_destination_module_gains_only_the_definition(self, tmp_path):
        project = _indexed(tmp_path, {**_LIB, "pkg/util.py": ""})

        code, _ = _cli(project, ["move-symbol", "pkg.lib:helper", "pkg.util"])

        assert code == 0
        assert (project / "pkg" / "util.py").read_text() == (
            'def helper(value):\n    """Help."""\n    return value + 1\n'
        )

    def test_an_import_the_destination_already_has_is_not_duplicated(self, tmp_path):
        project = _indexed(
            tmp_path,
            {
                "pkg/__init__.py": '"""pkg."""\n',
                "pkg/lib.py": (
                    '"""lib."""\nimport os\n\n\ndef helper(v):\n'
                    "    return os.sep + v\n"
                ),
                "pkg/util.py": '"""util."""\nimport os\n\n\ndef existing():\n    return os.sep\n',
                "app.py": "from pkg.lib import helper\n\n\ndef run():\n    return helper('a')\n",
            },
        )

        code, _ = _cli(project, ["move-symbol", "pkg.lib:helper", "pkg.util"])

        assert code == 0
        text = (project / "pkg" / "util.py").read_text()
        assert text.count("import os") == 1
        assert text.endswith("def helper(v):\n    return os.sep + v\n")


# ---------------------------------------------------------------------------
# The importer matrix
# ---------------------------------------------------------------------------


class TestImporterMatrix:
    def test_aliased_import_keeps_its_alias(self, tmp_path):
        project = _indexed(
            tmp_path,
            {
                **_LIB,
                "aliased.py": "from pkg.lib import helper as h\n\n\ndef go():\n    return h(1)\n",
            },
        )

        code, _ = _cli(project, ["move-symbol", "pkg.lib:helper", "pkg.util"])

        assert code == 0
        assert (project / "aliased.py").read_text() == (
            "from pkg.util import helper as h\n\n\ndef go():\n    return h(1)\n"
        )

    def test_a_multi_name_line_is_split_not_rewritten_wholesale(self, tmp_path):
        project = _indexed(
            tmp_path,
            {
                "pkg/__init__.py": '"""pkg."""\n',
                "pkg/lib.py": (
                    '"""lib."""\n\n\ndef helper(v):\n    return v\n\n\ndef other(v):\n    return v\n'
                ),
                "multi.py": (
                    "from pkg.lib import helper, other\n\n\ndef go():\n"
                    "    return helper(1), other(2)\n"
                ),
            },
        )

        code, _ = _cli(project, ["move-symbol", "pkg.lib:helper", "pkg.util"])

        assert code == 0
        assert (project / "multi.py").read_text() == (
            "from pkg.util import helper\n"
            "from pkg.lib import other\n"
            "\n\ndef go():\n    return helper(1), other(2)\n"
        )

    def test_a_barrel_reexport_is_rewritten_and_its_consumer_is_not(self, tmp_path):
        """The deliberate divergence from rename's ``--include-exports`` gate.

        A move does not change the exported *name*, so repairing the barrel
        is repair; the consumer that goes *through* the repaired barrel is
        already correct and gets no edit at all.
        """
        project = _indexed(
            tmp_path,
            {
                "pkg/__init__.py": '"""pkg."""\n\nfrom pkg.lib import helper\n\n__all__ = ["helper"]\n',
                "pkg/lib.py": '"""lib."""\n\n\ndef helper(v):\n    return v\n',
                "consumer.py": "from pkg import helper\n\n\ndef go():\n    return helper(1)\n",
            },
        )
        consumer_before = (project / "consumer.py").read_text()

        code, payload = _cli(project, ["move-symbol", "pkg.lib:helper", "pkg.util"])

        assert code == 0
        assert (project / "pkg" / "__init__.py").read_text() == (
            '"""pkg."""\n\nfrom pkg.util import helper\n\n__all__ = ["helper"]\n'
        )
        assert (project / "consumer.py").read_text() == consumer_before
        assert "consumer.py" not in payload["files_modified"]

        # The consumer still resolves, now through the repaired barrel.
        _cli(project, ["index", str(project)])
        code, refs = _cli(project, ["refs", "pkg.util:helper", "--all"])
        assert code == 0
        assert any(ref["location"]["file_path"] == "consumer.py" for ref in refs)

    def test_several_importers_are_all_rewritten_in_one_transaction(self, tmp_path):
        project = _indexed(
            tmp_path,
            {
                **_LIB,
                "two.py": "from pkg.lib import helper\n\n\ndef go():\n    return helper(2)\n",
                "three.py": "from pkg.lib import helper as h\n\n\ndef go():\n    return h(3)\n",
            },
        )

        code, payload = _cli(project, ["move-symbol", "pkg.lib:helper", "pkg.util"])

        assert code == 0
        assert set(payload["files_modified"]) == {
            "pkg/lib.py",
            "app.py",
            "two.py",
            "three.py",
        }
        for name in ("app.py", "two.py", "three.py"):
            assert "pkg.util" in (project / name).read_text()
            assert "pkg.lib" not in (project / name).read_text()


# ---------------------------------------------------------------------------
# The refusal matrix, keyed by precondition name
# ---------------------------------------------------------------------------


def _refuse(tmp_path, files, symbol_id, dest_module) -> SubmitError:
    """Submit a move expected to refuse; return the SubmitError."""
    from pypeeker.storage import IndexStore

    project = _indexed(tmp_path, files)
    store = IndexStore(project)
    intent = MoveSymbolIntent("move-symbol", symbol_id, dest_module)
    with pytest.raises(SubmitError) as excinfo:
        submit_intent(intent, store, TransactionStore(project))
    return excinfo.value


class TestRefusalMatrix:
    """Every refusal is a named precondition under the uniform CLI code.

    ``code`` is ``"plan-refused"`` throughout — deliberately: move-symbol is
    a CLI operation, not a ``check --fix`` remedy, so none of its
    preconditions carries a slug and the frozen refusal vocabulary
    (``tests/test_refusal_vocabulary.py``) is untouched.
    """

    def test_missing_symbol(self, tmp_path):
        error = _refuse(tmp_path, dict(_LIB), "pkg.lib:nope", "pkg.util")
        assert error.code == "plan-refused"
        assert error.precondition == "symbol-resolves-uniquely"

    def test_not_a_top_level_definition(self, tmp_path):
        files = {
            **_LIB,
            "pkg/klass.py": '"""k."""\n\n\nclass Svc:\n    def run(self):\n        return 1\n',
        }
        error = _refuse(tmp_path, files, "pkg.klass:Svc.run", "pkg.util")
        assert error.code == "plan-refused"
        assert error.precondition == "top-level-definition"
        assert "module-level functions and classes only" in error.detail

    def test_a_variable_is_not_movable(self, tmp_path):
        files = {**_LIB, "pkg/consts.py": '"""c."""\n\nLIMIT = 3\n'}
        error = _refuse(tmp_path, files, "pkg.consts:LIMIT", "pkg.util")
        assert error.precondition == "top-level-definition"

    def test_invalid_destination_module(self, tmp_path):
        error = _refuse(tmp_path, dict(_LIB), "pkg.lib:helper", "pkg/util.py")
        assert error.precondition == "valid-module-path"

    def test_moving_a_symbol_to_its_own_module(self, tmp_path):
        error = _refuse(tmp_path, dict(_LIB), "pkg.lib:helper", "pkg.lib")
        assert error.precondition == "move-is-not-self"

    def test_destination_exists_on_disk_but_is_unindexed(self, tmp_path):
        project = _project(tmp_path, dict(_LIB))
        code, _ = _cli(project, ["index", str(project)])
        assert code == 0
        (project / "pkg" / "util.py").write_text('"""util."""\n')

        code, payload = _cli(
            project, ["move-symbol", "pkg.lib:helper", "pkg.util", "--no-refresh"]
        )

        assert code == 1
        assert payload["code"] == "plan-refused"
        assert "not indexed" in payload["error"]

    def test_name_collision_at_the_destination(self, tmp_path):
        files = {
            **_LIB,
            "pkg/util.py": '"""util."""\n\n\ndef helper():\n    return 0\n',
        }
        error = _refuse(tmp_path, files, "pkg.lib:helper", "pkg.util")
        assert error.precondition == "no-destination-name-collision"
        assert "already bound at module level" in error.detail

    def test_an_import_binding_counts_as_a_collision(self, tmp_path):
        files = {
            **_LIB,
            "pkg/util.py": '"""util."""\n\nfrom pkg.lib import helper\n',
        }
        error = _refuse(tmp_path, files, "pkg.lib:helper", "pkg.util")
        assert error.precondition == "no-destination-name-collision"

    def test_an_aliased_import_of_the_target_at_the_destination(self, tmp_path):
        """The destination would end up importing the symbol from itself."""
        files = {
            **_LIB,
            "pkg/util.py": '"""util."""\n\nfrom pkg.lib import helper as h\n\n\ndef go():\n    return h(1)\n',
        }
        error = _refuse(tmp_path, files, "pkg.lib:helper", "pkg.util")
        assert error.precondition == "no-destination-name-collision"
        assert "importing itself" in error.detail

    def test_a_star_import_at_the_destination_hides_the_surface(self, tmp_path):
        files = {
            **_LIB,
            "pkg/util.py": '"""util."""\n\nfrom os.path import *\n',
        }
        error = _refuse(tmp_path, files, "pkg.lib:helper", "pkg.util")
        assert error.precondition == "no-destination-name-collision"
        assert "star import" in error.detail

    def test_the_qualified_import_form_refuses_by_name(self, tmp_path):
        files = {
            **_LIB,
            "qualified.py": "import pkg.lib\n\n\ndef go():\n    return pkg.lib.helper(1)\n",
        }
        error = _refuse(tmp_path, files, "pkg.lib:helper", "pkg.util")
        assert error.code == "plan-refused"
        assert error.precondition == "move-qualified-use-unsupported"
        assert "refused rather than half-rewritten" in error.detail

    @pytest.mark.parametrize(
        "source",
        [
            "from pkg import lib\n\n\ndef go():\n    return lib.helper(1)\n",
            "import pkg.lib as L\n\n\ndef go():\n    return L.helper(1)\n",
            "import pkg.lib\n\n\ndef go():\n    return pkg.lib.helper(1)\n",
        ],
        ids=["from-package", "aliased-module", "dotted-module"],
    )
    def test_every_qualified_spelling_is_caught(self, tmp_path, source):
        """The dotted spelling is the one the resolver alone cannot attribute."""
        error = _refuse(
            tmp_path, {**_LIB, "qualified.py": source}, "pkg.lib:helper", "pkg.util"
        )
        assert error.precondition == "move-qualified-use-unsupported"
        assert "qualified.py:5" in error.detail

    def test_an_unrelated_attribute_on_the_same_module_import_is_not_a_refusal(
        self, tmp_path
    ):
        """The qualified check must key on the moved name, not on the import."""
        files = {
            **_LIB,
            "pkg/lib.py": (
                '"""lib."""\n\n\ndef helper(value):\n    """Help."""\n    return value + 1\n'
                "\n\ndef other():\n    return 2\n"
            ),
            "qualified.py": "import pkg.lib\n\n\ndef go():\n    return pkg.lib.other()\n",
        }
        project = _indexed(tmp_path, files)

        code, _ = _cli(project, ["move-symbol", "pkg.lib:helper", "pkg.util"])

        assert code == 0

    def test_a_body_using_a_sibling_definition_refuses(self, tmp_path):
        files = {
            "pkg/__init__.py": '"""pkg."""\n',
            "pkg/lib.py": (
                '"""lib."""\n\n\ndef sibling():\n    return 1\n\n\n'
                "def helper(v):\n    return sibling() + v\n"
            ),
            "app.py": "from pkg.lib import helper\n\n\ndef run():\n    return helper(1)\n",
        }
        error = _refuse(tmp_path, files, "pkg.lib:helper", "pkg.util")
        assert error.precondition == "moved-body-closed"
        assert "which stays in the source module" in error.detail

    def test_a_body_using_a_module_constant_refuses(self, tmp_path):
        files = {
            "pkg/__init__.py": '"""pkg."""\n',
            "pkg/lib.py": '"""lib."""\n\nLIMIT = 3\n\n\ndef helper(v):\n    return v + LIMIT\n',
            "app.py": "from pkg.lib import helper\n\n\ndef run():\n    return helper(1)\n",
        }
        error = _refuse(tmp_path, files, "pkg.lib:helper", "pkg.util")
        assert error.precondition == "moved-body-closed"

    def test_an_annotation_only_free_name_is_caught_too(self, tmp_path):
        """Return annotations are recorded in the *module* scope, not the body's.

        Keyed on ``Reference.in_scope_id`` this would slip through and land a
        definition at the destination whose annotation names a class that
        stayed behind, so ``MovedBodyClosed`` keys on the line span instead.
        """
        files = {
            "pkg/__init__.py": '"""pkg."""\n',
            "pkg/lib.py": (
                '"""lib."""\n\n\nclass Result:\n    pass\n\n\n'
                "def helper(v) -> Result:\n    return v\n"
            ),
            "app.py": "from pkg.lib import helper\n\n\ndef run():\n    return helper(1)\n",
        }
        error = _refuse(tmp_path, files, "pkg.lib:helper", "pkg.util")
        assert error.precondition == "moved-body-closed"
        assert "Result" in error.detail

    def test_the_source_module_still_using_the_symbol_refuses(self, tmp_path):
        files = {
            "pkg/__init__.py": '"""pkg."""\n',
            "pkg/lib.py": (
                '"""lib."""\n\n\ndef helper(v):\n    return v\n\n\n'
                "def caller():\n    return helper(1)\n"
            ),
            "app.py": "from pkg.lib import helper\n\n\ndef run():\n    return helper(1)\n",
        }
        error = _refuse(tmp_path, files, "pkg.lib:helper", "pkg.util")
        assert error.precondition == "source-module-free"
        assert "does not add back-imports" in error.detail

    def test_an_all_entry_in_the_source_module_refuses(self, tmp_path):
        """``__all__`` entries are strings: no reference points at them.

        ``source-module-free`` reasons over references and is structurally
        blind to this, which is why it gets its own named check rather than a
        broader one.
        """
        files = {
            "pkg/__init__.py": '"""pkg."""\n',
            "pkg/lib.py": (
                '"""lib."""\n\n__all__ = [\n    "helper",\n]\n\n\n'
                "def helper(v):\n    return v\n"
            ),
            "app.py": "from pkg.lib import helper\n\n\ndef run():\n    return helper(1)\n",
        }
        error = _refuse(tmp_path, files, "pkg.lib:helper", "pkg.util")
        assert error.precondition == "source-export-list-clean"
        assert "edit __all__ first" in error.detail

    def test_an_all_list_naming_other_symbols_is_not_a_refusal(self, tmp_path):
        files = {
            "pkg/__init__.py": '"""pkg."""\n',
            "pkg/lib.py": (
                '"""lib."""\n\n__all__ = ["other"]\n\n\ndef other():\n    return 0\n'
                "\n\ndef helper(v):\n    return v\n"
            ),
            "app.py": "from pkg.lib import helper\n\n\ndef run():\n    return helper(1)\n",
        }
        project = _indexed(tmp_path, files)

        code, _ = _cli(project, ["move-symbol", "pkg.lib:helper", "pkg.util"])

        assert code == 0

    def test_an_augmented_all_refuses_like_a_plain_one(self, tmp_path):
        """``__all__ += [...]`` is an ``augmented_assignment``, not an ``assignment``.

        Scanning only the plain form made the refusal depend on statement
        shape rather than on what the export list says, so the same module
        exporting the same name refused one way and applied the other —
        leaving ``__all__ += ["helper"]`` behind with no ``helper``, i.e.
        exactly the ``AttributeError`` on ``from pkg.lib import *`` the class
        exists to prevent.
        """
        files = {
            "pkg/__init__.py": '"""pkg."""\n',
            "pkg/lib.py": (
                '"""lib."""\n\n__all__ = ["other"]\n__all__ += ["helper"]\n\n'
                "other = 2\n\n\ndef helper(v):\n    return v\n"
            ),
            "app.py": "from pkg.lib import helper\n\n\ndef run():\n    return helper(1)\n",
        }
        error = _refuse(tmp_path, files, "pkg.lib:helper", "pkg.util")
        assert error.precondition == "source-export-list-clean"
        assert "edit __all__ first" in error.detail

    def test_an_all_built_in_an_unreadable_shape_refuses(self, tmp_path):
        """A declared-but-unreadable ``__all__`` is refused, not waved through.

        ``__all__.extend(...)`` names no statement this check can scan. The
        module says it has an export list; the honest answer is that its
        contents are unknown, not that the name is absent.
        """
        files = {
            "pkg/__init__.py": '"""pkg."""\n',
            "pkg/lib.py": (
                '"""lib."""\n\n__all__ = []\n__all__.extend(["helper"])\n\n\n'
                "def helper(v):\n    return v\n"
            ),
            "app.py": "from pkg.lib import helper\n\n\ndef run():\n    return helper(1)\n",
        }
        error = _refuse(tmp_path, files, "pkg.lib:helper", "pkg.util")
        assert error.precondition == "source-export-list-clean"
        assert "cannot be determined" in error.detail

    def test_a_quoted_annotation_free_name_refuses_like_an_unquoted_one(
        self, tmp_path
    ):
        """The guard's verdict must not flip on a pair of quotes.

        A string annotation produces no ``Reference``, so the reference-keyed
        free-name check could not see it — and ``def helper(v) -> "Result"``
        landed at a destination where ``Result`` is undefined, breaking
        ``typing.get_type_hints`` and every consumer that resolves
        annotations.
        """
        files = {
            "pkg/__init__.py": '"""pkg."""\n',
            "pkg/lib.py": (
                '"""lib."""\n\n\nclass Result:\n    pass\n\n\n'
                'def helper(v) -> "Result":\n    return v\n'
            ),
            "app.py": "from pkg.lib import helper\n\n\ndef run():\n    return helper(1)\n",
        }
        error = _refuse(tmp_path, files, "pkg.lib:helper", "pkg.util")
        assert error.precondition == "moved-body-closed"
        assert "string annotation" in error.detail
        assert "Result" in error.detail

    def test_a_quoted_annotation_naming_an_import_travels_with_the_body(
        self, tmp_path
    ):
        """Refusal is for names that *stay*; an import is carried, quoted or not."""
        files = {
            "pkg/__init__.py": '"""pkg."""\n',
            "pkg/types.py": '"""types."""\n\n\nclass Thing:\n    pass\n',
            "pkg/lib.py": (
                '"""lib."""\n\nfrom pkg.types import Thing\n\n\n'
                'def helper(v: "Thing"):\n    return v\n'
            ),
            "app.py": "from pkg.lib import helper\n\n\ndef run():\n    return helper(1)\n",
        }
        project = _indexed(tmp_path, files)

        code, _ = _cli(project, ["move-symbol", "pkg.lib:helper", "pkg.util"])

        assert code == 0
        assert "from pkg.types import Thing" in (project / "pkg" / "util.py").read_text()

    def test_a_conditionally_defined_function_refuses(self, tmp_path):
        """``if TYPE_CHECKING:`` opens no scope, so only the span tells them apart.

        The indented slice parses without error under tree-sitter, so the
        "parses on its own" check waved it through and the move corrupted
        *both* files: an ``if`` with no block in the source, an over-indented
        ``def`` at the destination.
        """
        files = {
            "pkg/__init__.py": '"""pkg."""\n',
            "pkg/lib.py": (
                '"""lib."""\n\nfrom typing import TYPE_CHECKING\n\n'
                "if TYPE_CHECKING:\n\n    def helper(v):\n        return v\n"
            ),
        }
        error = _refuse(tmp_path, files, "pkg.lib:helper", "pkg.util")
        assert error.precondition == "unconditional-definition"
        assert "nested block" in error.detail

    def test_a_star_import_of_the_source_module_refuses(self, tmp_path):
        """The one import edge ``find_importers`` structurally cannot produce.

        A star binds the local name ``*``, so no edge is collected and the
        "every collected edge is rewritten" contract held vacuously while
        ``star.use()`` was left raising ``NameError``.
        """
        files = {
            "pkg/__init__.py": '"""pkg."""\n',
            "pkg/lib.py": '"""lib."""\n\n\ndef helper(v):\n    return v\n',
            "pkg/star.py": (
                '"""star."""\n\nfrom pkg.lib import *\n\n\ndef use():\n'
                "    return helper(1)\n"
            ),
        }
        error = _refuse(tmp_path, files, "pkg.lib:helper", "pkg.util")
        assert error.precondition == "source-star-import-opaque"
        assert "pkg/star.py" in error.detail

    def test_a_barrel_star_reexport_of_the_source_module_refuses(self, tmp_path):
        """Same edge one level out: the package's public surface loses the name."""
        files = {
            "pkg/__init__.py": '"""pkg."""\n\nfrom pkg.lib import *\n',
            "pkg/lib.py": '"""lib."""\n\n\ndef helper(v):\n    return v\n',
            "app.py": "from pkg import helper\n\n\ndef run():\n    return helper(1)\n",
        }
        error = _refuse(tmp_path, files, "pkg.lib:helper", "pkg.util")
        assert error.precondition == "source-star-import-opaque"

    def test_a_destination_under_a_module_ancestor_refuses(self, tmp_path):
        """``pkg.lib`` is a file, so ``pkg.lib.sub`` names no importable location.

        Creating ``pkg/lib/sub.py`` beside ``pkg/lib.py`` and rewriting every
        importer to it yields ``ModuleNotFoundError: ... 'pkg.lib' is not a
        package`` — a file born where Python will never look for it.
        """
        error = _refuse(tmp_path, dict(_LIB), "pkg.lib:helper", "pkg.lib.sub")
        assert error.precondition == "destination-path-unobstructed"
        assert "not a" in error.detail and "package" in error.detail

    def test_a_destination_shadowing_a_package_directory_refuses(self, tmp_path):
        """A module file beats a namespace portion in Python's finder.

        Creating ``pkg/inner.py`` next to an existing ``pkg/inner/`` makes
        ``pkg.inner.deep`` unimportable — the move breaking modules it never
        touched.
        """
        files = {
            **_LIB,
            "pkg/inner/deep.py": '"""deep."""\n\n\ndef deepfn():\n    return 1\n',
        }
        error = _refuse(tmp_path, files, "pkg.lib:helper", "pkg.inner")
        assert error.precondition == "destination-path-unobstructed"
        assert "package directory" in error.detail

    def test_a_decorated_definition_refuses(self, tmp_path):
        files = {
            "pkg/__init__.py": '"""pkg."""\n',
            "pkg/lib.py": (
                '"""lib."""\n\n\ndef deco(f):\n    return f\n\n\n'
                "@deco\ndef helper(v):\n    return v\n"
            ),
            "app.py": "from pkg.lib import helper\n\n\ndef run():\n    return helper(1)\n",
        }
        error = _refuse(tmp_path, files, "pkg.lib:helper", "pkg.util")
        assert error.precondition == "undecorated-definition"

    def test_a_parenthesized_importer_line_refuses(self, tmp_path):
        files = {
            **_LIB,
            "paren.py": "from pkg.lib import (helper)\n\n\ndef go():\n    return helper(1)\n",
        }
        error = _refuse(tmp_path, files, "pkg.lib:helper", "pkg.util")
        assert error.precondition == "import-line-surgery-safe"

    def test_the_cli_reports_a_refusal_as_plan_refused(self, tmp_path):
        project = _indexed(tmp_path, dict(_LIB))
        before = _snapshot(project)

        code, payload = _cli(project, ["move-symbol", "pkg.lib:helper", "pkg.lib"])

        assert code == 1
        assert payload["code"] == "plan-refused"
        assert set(payload) == {"error", "code"}
        assert _snapshot(project) == before


# ---------------------------------------------------------------------------
# A move inside a batch
# ---------------------------------------------------------------------------


class TestMoveInABatch:
    def test_a_move_flattens_with_another_intent_into_one_transaction(self, tmp_path):
        """The whole point of PR1 + PR2: a birth survives the flatten."""
        from pypeeker.refactor.applier import TransactionApplier
        from pypeeker.storage import IndexStore

        project = _indexed(
            tmp_path,
            {
                **_LIB,
                "other.py": '"""other."""\n\n\ndef renamed_me():\n    return 1\n',
            },
        )
        store = IndexStore(project)
        before = _snapshot(project)

        result = run_batch(
            [
                MoveSymbolIntent("move", "pkg.lib:helper", "pkg.util"),
                RenameIntent("ren", "other:renamed_me", "renamed_you"),
            ],
            store,
            tx_store=TransactionStore(tmp_path / "scratch"),
            policy=BatchPolicy.ALL_OR_NOTHING,
        )

        assert [e.intent.intent_id for e in result.executed] == ["move", "ren"]
        assert result.effect.files_created == {"pkg/util.py"}

        flattened = flatten_batch(result, store)
        assert [create.path for create in flattened.creates] == ["pkg/util.py"]
        assert {edit.file for edit in flattened.edits} == {
            "pkg/lib.py",
            "app.py",
            "other.py",
        }

        tx_store = TransactionStore(project)
        tx_store.save(
            flattened.header,
            flattened.edits,
            None,
            creates=flattened.creates,
            deletes=flattened.deletes,
        )
        applier = TransactionApplier(store, tx_store)
        applied = applier.apply(flattened.header.tx_id)

        assert applied["files_created"] == ["pkg/util.py"]
        assert "def helper(value):" in (project / "pkg" / "util.py").read_text()
        assert "renamed_you" in (project / "other.py").read_text()

        applier.rollback(flattened.header.tx_id)
        assert _snapshot(project) == before

    def test_the_schedule_is_input_order_independent_either_side_of_existence(
        self, tmp_path
    ):
        """Adjustment (a) proved at the scheduler: same order, both worlds."""
        from pypeeker.refactor.batch import schedule
        from pypeeker.storage import IndexStore

        move = MoveSymbolIntent("move", "pkg.lib:helper", "pkg.util")
        rename = RenameIntent("ren", "app:run", "go")
        absent = IndexStore(_indexed(tmp_path / "absent", dict(_LIB)))
        present = IndexStore(
            _indexed(tmp_path / "present", {**_LIB, "pkg/util.py": '"""util."""\n'})
        )

        def _order(store, intents):
            return tuple(i.intent_id for i in schedule(list(intents), store).ordered)

        assert _order(absent, [move, rename]) == _order(absent, [rename, move])
        assert _order(present, [move, rename]) == _order(present, [rename, move])
        assert _order(absent, [move, rename]) == _order(present, [move, rename])
        # The footprint each key is derived from is identical too.
        assert move.footprint(absent) == move.footprint(present)

    def test_submit_intent_carries_the_file_lifecycle_channel_out(self, tmp_path):
        """``ExecutedIntent`` must not truncate the birth the planner declared.

        ``submit_intent`` rebuilds a ``Materialized`` from the engine's
        record, so a channel missing from ``ExecutedIntent`` is a channel the
        returned object cannot have. For a newborn destination the move emits
        no edit against it at all — the whole content rides in the create
        entry — so a truncated result reads as "delete the definition, rewrite
        every importer to a module nobody creates", with no refusal anywhere.
        """
        from pypeeker.storage import IndexStore

        project = _indexed(tmp_path, dict(_LIB))
        store = IndexStore(project)

        materialized = submit_intent(
            MoveSymbolIntent("mv", "pkg.lib:helper", "pkg.util"),
            store,
            TransactionStore(project),
        )

        assert set(materialized.files_created) == {"pkg/util.py"}
        assert materialized.files_created["pkg/util.py"].startswith(
            b'"""pkg.util."""'
        )
        assert materialized.files_deleted == []
        assert {edit.file for edit in materialized.edits} == {"pkg/lib.py", "app.py"}


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


class TestWiring:
    def test_the_kind_is_registered_with_a_materializer(self):
        from pypeeker.refactor.registry import get_materializer

        assert get_materializer("move-symbol") is not None

    def test_plan_batch_accepts_a_move_symbol_entry(self, indexed_project):
        from pypeeker.app.batch_intents import build_batch_intents

        project, store = indexed_project(dict(_LIB))
        [intent] = build_batch_intents(
            [
                {
                    "kind": "move-symbol",
                    "symbol_id": "pkg.lib:helper",
                    "dest_module": "pkg.util",
                }
            ],
            store,
            project,
        )

        assert isinstance(intent, MoveSymbolIntent)
        assert intent.intent_id == "move-symbol-1"
        assert intent.dest_module == "pkg.util"

    def test_the_command_is_documented(self):
        result = CliRunner().invoke(main, ["move-symbol", "--help"])
        assert result.exit_code == 0
        assert "SYMBOL_ID" in result.output
        assert "DEST_MODULE" in result.output
        assert "--plan" in result.output
