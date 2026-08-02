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
# Where a carried import lands in an existing destination
# ---------------------------------------------------------------------------


_CARRY = {
    "pkg/__init__.py": '"""pkg."""\n',
    "pkg/lib.py": '"""lib."""\nimport os\n\n\ndef helper(v):\n    return os.sep + v\n',
    "app.py": "from pkg.lib import helper\n\n\ndef run():\n    return helper('a')\n",
}
"""``helper`` needs ``os``, so every move out of ``pkg.lib`` carries one import."""


def _moved_into(tmp_path: Path, destination: str) -> tuple[Path, dict]:
    """Move ``pkg.lib:helper`` into a ``pkg/util.py`` that already reads ``destination``."""
    project = _indexed(tmp_path, {**_CARRY, "pkg/util.py": destination})
    code, payload = _cli(project, ["move-symbol", "pkg.lib:helper", "pkg.util"])
    assert code == 0
    return project, payload


class TestDestinationImportPlacement:
    """A carried import joins the destination's top import block, not the tail.

    Before this behavior existed the imports the moved body needs were written
    immediately above the appended definition, so extending any destination
    that already had code produced an import *after* code — ``E402`` in every
    project that lints it, and unreadable in every project that does not. The
    placement is decided from the CST's header run (comments, an optional
    module docstring, imports; the first other node ends it), which is what
    makes it deterministic in the shapes a text heuristic could not survive.
    """

    def test_a_carried_import_joins_the_top_import_block(self, tmp_path):
        """The ``__future__`` line keeps its place; the new import follows the last one."""
        destination = (
            '"""util."""\n'
            "\n"
            "from __future__ import annotations\n"
            "\n"
            "import sys\n"
            "\n\n"
            "def existing():\n"
            "    return sys.platform\n"
        )
        project = _indexed(tmp_path, {**_CARRY, "pkg/util.py": destination})
        before = _snapshot(project)

        code, payload = _cli(project, ["move-symbol", "pkg.lib:helper", "pkg.util"])

        assert code == 0
        assert (project / "pkg" / "util.py").read_text() == (
            '"""util."""\n'
            "\n"
            "from __future__ import annotations\n"
            "\n"
            "import sys\n"
            "import os\n"
            "\n\n"
            "def existing():\n"
            "    return sys.platform\n"
            "\n\n"
            "def helper(v):\n"
            "    return os.sep + v\n"
        )

        # Two edits on one file still invert exactly.
        code, _ = _cli(project, ["rollback", payload["tx_id"]])
        assert code == 0
        assert _snapshot(project) == before

    def test_a_destination_with_only_a_docstring_gains_a_new_import_block(
        self, tmp_path
    ):
        """No imports to join, so the run ends at the docstring and a block starts below it."""
        project, _ = _moved_into(
            tmp_path, '"""util."""\n\n\ndef existing():\n    return 0\n'
        )

        assert (project / "pkg" / "util.py").read_text() == (
            '"""util."""\n'
            "\n"
            "import os\n"
            "\n\n"
            "def existing():\n"
            "    return 0\n"
            "\n\n"
            "def helper(v):\n"
            "    return os.sep + v\n"
        )

    def test_a_destination_with_no_header_gets_the_import_first(self, tmp_path):
        """An empty header run anchors at byte 0 — the import opens the file."""
        project, _ = _moved_into(tmp_path, "def existing():\n    return 0\n")

        assert (project / "pkg" / "util.py").read_text() == (
            "import os\n"
            "\n\n"
            "def existing():\n"
            "    return 0\n"
            "\n\n"
            "def helper(v):\n"
            "    return os.sep + v\n"
        )

    def test_a_conditional_block_at_the_top_does_not_swallow_the_anchor(
        self, tmp_path
    ):
        """``if`` is not an import statement, so it ends the header run untouched.

        This is the shape the old placement rule named as the reason no
        heuristic could work. The CST answers it: the guarded import keeps its
        guard and its position, and the carried import joins the plain block
        above it.
        """
        project, _ = _moved_into(
            tmp_path,
            '"""util."""\n'
            "import sys\n"
            "\n"
            "if True:\n"
            "    import json\n"
            "\n\n"
            "def existing():\n"
            "    return sys.platform + str(json)\n",
        )

        assert (project / "pkg" / "util.py").read_text() == (
            '"""util."""\n'
            "import sys\n"
            "import os\n"
            "\n"
            "if True:\n"
            "    import json\n"
            "\n\n"
            "def existing():\n"
            "    return sys.platform + str(json)\n"
            "\n\n"
            "def helper(v):\n"
            "    return os.sep + v\n"
        )

    def test_a_destination_that_is_only_imports_keeps_the_single_edit_shape(
        self, tmp_path
    ):
        """The header reaches the trailing whitespace, so one edit does the whole job.

        Splitting here would put an insert and the trailing splice at the same
        start offset, and both bottom-to-top splicers sort by start, so the
        order between them would be undefined and the splice's ``old`` check
        would read bytes the insert had moved. Falling back costs nothing:
        "above the definition" already *is* "after the last import".
        """
        project, _ = _moved_into(tmp_path, '"""util."""\nimport sys\n')

        assert (project / "pkg" / "util.py").read_text() == (
            '"""util."""\n'
            "import sys\n"
            "\n"
            "import os\n"
            "\n\n"
            "def helper(v):\n"
            "    return os.sep + v\n"
        )

    def test_a_comment_before_a_definition_is_not_separated_from_it(self, tmp_path):
        """Comments are traversed by the header run but never set the anchor.

        A comment sitting just above the first real statement documents that
        statement. Anchoring after it would splice generated imports between a
        human's comment and the definition it explains, so the anchor is the
        last header *import* instead.
        """
        project, _ = _moved_into(
            tmp_path,
            '"""util."""\n'
            "import sys\n"
            "# explains what follows\n"
            "\n\n"
            "def existing():\n"
            "    return sys.platform\n",
        )

        assert (project / "pkg" / "util.py").read_text() == (
            '"""util."""\n'
            "import sys\n"
            "import os\n"
            "# explains what follows\n"
            "\n\n"
            "def existing():\n"
            "    return sys.platform\n"
            "\n\n"
            "def helper(v):\n"
            "    return os.sep + v\n"
        )

    def test_a_comment_only_header_keeps_the_import_below_the_comment(self, tmp_path):
        """With no docstring and no imports the comment run still bounds the anchor.

        Byte 0 is where an empty header run anchors, and a licence header or a
        shebang is exactly the text an import must not jump above.
        """
        project, _ = _moved_into(
            tmp_path, "# license header\n\n\ndef existing():\n    return 0\n"
        )

        assert (project / "pkg" / "util.py").read_text() == (
            "# license header\n"
            "\n"
            "import os\n"
            "\n\n"
            "def existing():\n"
            "    return 0\n"
            "\n\n"
            "def helper(v):\n"
            "    return os.sep + v\n"
        )

    def test_an_unparseable_destination_falls_back_to_the_single_edit(self, tmp_path):
        """A broken CST degenerates to ``ERROR`` nodes and an empty header run.

        Anchoring at byte 0 on that evidence would splice an import into
        whatever broke the parse, so the anchor reports "no answer" and the
        move writes the imports above the definition, as it always did.
        """
        project, _ = _moved_into(
            tmp_path, '"""util."""\nimport sys\n\ndef existing(:\n    return 0\n'
        )

        assert (project / "pkg" / "util.py").read_text() == (
            '"""util."""\n'
            "import sys\n"
            "\n"
            "def existing(:\n"
            "    return 0\n"
            "\n"
            "import os\n"
            "\n\n"
            "def helper(v):\n"
            "    return os.sep + v\n"
        )

    def test_the_split_survives_a_batch_replanned_against_the_overlay(self, tmp_path):
        """The batch engine splices a planner's edits itself, against simulated bytes.

        ``refactor.batch._splice`` is a second bottom-to-top splicer, so the
        two-edit destination has to be ordered correctly there too — and no
        other batch test extends an existing destination, they all create one.
        Mis-ordering is not silent there: the splice verifies each edit's
        ``old`` text, so the move would be reported as a precondition failure
        and never reach ``executed``.
        """
        from pypeeker.refactor.applier import TransactionApplier
        from pypeeker.storage import IndexStore

        project = _indexed(
            tmp_path,
            {
                **_CARRY,
                "pkg/util.py": (
                    '"""util."""\nimport sys\n\n\ndef existing():\n    return sys.platform\n'
                ),
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
        assert [entry.intent.intent_id for entry in result.executed] == ["move", "ren"]

        flattened = flatten_batch(result, store)

        tx_store = TransactionStore(project)
        tx_store.save(
            flattened.header,
            flattened.edits,
            None,
            creates=flattened.creates,
            deletes=flattened.deletes,
        )
        applier = TransactionApplier(store, tx_store)
        applier.apply(flattened.header.tx_id)

        assert (project / "pkg" / "util.py").read_text() == (
            '"""util."""\n'
            "import sys\n"
            "import os\n"
            "\n\n"
            "def existing():\n"
            "    return sys.platform\n"
            "\n\n"
            "def helper(v):\n"
            "    return os.sep + v\n"
        )

        applier.rollback(flattened.header.tx_id)
        assert _snapshot(project) == before


class TestHeaderAnchorIsAStatementBoundary:
    """The header run ends at a *statement* boundary, not at a line boundary.

    The two coincide for every header written in the ordinary way and diverge
    on one shape: a semicolon-joined module-level line. Anchoring at the end
    of the *line* containing the last header import puts the carried import
    inside whatever statement shares that line — and when that statement is a
    multi-line one, inside its continuation, producing a destination that does
    not parse at all while the CLI reports success. So a header node another
    statement shares a line with ends the run *before* itself, which places
    carried imports above the joined line: still inside the header, still
    ahead of any code.
    """

    def test_a_semicolon_joined_multi_line_statement_is_not_spliced_into(
        self, tmp_path
    ):
        """The regression case: the anchor used to land inside the tuple."""
        import ast

        destination = (
            '"""util."""\n'
            "\n"
            "import sys; VALUES = (\n"
            "    1,\n"
            ")\n"
            "\n\n"
            "def existing():\n"
            "    return sys.platform + str(VALUES)\n"
        )
        project = _indexed(tmp_path, {**_CARRY, "pkg/util.py": destination})
        before = _snapshot(project)

        code, payload = _cli(project, ["move-symbol", "pkg.lib:helper", "pkg.util"])

        assert code == 0
        text = (project / "pkg" / "util.py").read_text()
        assert text == (
            '"""util."""\n'
            "\n"
            "import os\n"
            "\n"
            "import sys; VALUES = (\n"
            "    1,\n"
            ")\n"
            "\n\n"
            "def existing():\n"
            "    return sys.platform + str(VALUES)\n"
            "\n\n"
            "def helper(v):\n"
            "    return os.sep + v\n"
        )
        # The property the byte assertion exists to protect.
        ast.parse(text)

        code, _ = _cli(project, ["rollback", payload["tx_id"]])
        assert code == 0
        assert _snapshot(project) == before

    def test_a_semicolon_joined_single_line_still_keeps_the_import_off_code(
        self, tmp_path
    ):
        """The milder same-cause case: parseable before, but ``E402``.

        The joined line binds ``VERSION`` — code — so the run cannot include
        it, and the carried import goes above it rather than between the
        ``import sys`` and the assignment it shares a line with.
        """
        project, _ = _moved_into(
            tmp_path,
            '"""util."""\n'
            "\n"
            "import sys; VERSION = 1\n"
            "\n\n"
            "def existing():\n"
            "    return sys.platform, VERSION\n",
        )

        assert (project / "pkg" / "util.py").read_text() == (
            '"""util."""\n'
            "\n"
            "import os\n"
            "\n"
            "import sys; VERSION = 1\n"
            "\n\n"
            "def existing():\n"
            "    return sys.platform, VERSION\n"
            "\n\n"
            "def helper(v):\n"
            "    return os.sep + v\n"
        )

    def test_a_trailing_comment_does_not_end_the_header_run(self, tmp_path):
        """The false positive to avoid: ``# noqa`` is not another statement.

        Only a real statement sharing the line ends the run early, so an
        import carrying a trailing comment still anchors *after* itself.
        """
        project, _ = _moved_into(
            tmp_path,
            '"""util."""\n'
            "\n"
            "import sys  # noqa: F401\n"
            "\n\n"
            "def existing():\n"
            "    return 0\n",
        )

        assert (project / "pkg" / "util.py").read_text() == (
            '"""util."""\n'
            "\n"
            "import sys  # noqa: F401\n"
            "import os\n"
            "\n\n"
            "def existing():\n"
            "    return 0\n"
            "\n\n"
            "def helper(v):\n"
            "    return os.sep + v\n"
        )


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
# Conditionally bound imports the move would carry (TASK-132, advisory 1)
# ---------------------------------------------------------------------------


class TestConditionalCarriedImports:
    """A carried import that the source binds under a guard refuses by name.

    ``move-symbol`` writes every carried binding as a plain module-level
    ``import`` statement. Before ``carried-imports-unconditional`` existed the
    guard was simply dropped: the moves in the first two tests below both
    exited 0 and wrote an *unguarded, run-time* import into the destination —
    exactly the heavy or circular import the ``if TYPE_CHECKING:`` guard was
    written to prevent, and the loss of the ``except ImportError`` fallback in
    the second. The guard is invisible to the semantic index (``if`` and
    ``try`` open no scope, so the binding still records the module as its
    parent scope), so the check reads the CST.
    """

    def test_a_type_checking_guarded_import_the_body_needs_refuses(self, tmp_path):
        files = {
            "pkg/__init__.py": '"""pkg."""\n',
            "pkg/heavy.py": '"""heavy."""\n\n\nclass Heavy:\n    pass\n',
            "pkg/lib.py": (
                '"""lib."""\n'
                "from typing import TYPE_CHECKING\n"
                "\n"
                "if TYPE_CHECKING:\n"
                "    from pkg.heavy import Heavy\n"
                "\n\n"
                'def helper(value: "Heavy") -> int:\n'
                "    return 1\n"
            ),
            "app.py": "from pkg.lib import helper\n\n\ndef run():\n    return helper(None)\n",
        }

        error = _refuse(tmp_path, files, "pkg.lib:helper", "pkg.util")

        assert error.code == "plan-refused"
        assert error.precondition == "carried-imports-unconditional"
        assert "if TYPE_CHECKING:" in error.detail
        assert "'Heavy'" in error.detail
        assert "pkg/lib.py:5" in error.detail
        assert "Promote the guard in the source module first" in error.detail

    def test_a_try_except_import_fallback_refuses_too(self, tmp_path):
        files = {
            "pkg/__init__.py": '"""pkg."""\n',
            "pkg/lib.py": (
                '"""lib."""\n'
                "try:\n"
                "    import ujson as json\n"
                "except ImportError:\n"
                "    import json\n"
                "\n\n"
                "def helper(value):\n"
                "    return json.dumps(value)\n"
            ),
            "app.py": "from pkg.lib import helper\n\n\ndef run():\n    return helper({})\n",
        }

        error = _refuse(tmp_path, files, "pkg.lib:helper", "pkg.util")

        assert error.code == "plan-refused"
        assert error.precondition == "carried-imports-unconditional"
        assert "try:" in error.detail
        assert "'json'" in error.detail

    def test_a_parenthesized_multi_line_import_is_not_a_guard(self, tmp_path):
        """The false-positive guard: indentation is not the signal, nesting is.

        Every name in a parenthesized continuation line is indented exactly
        like a guarded one, so a column-based check would refuse this move.
        The statement's CST parent is still ``module``, so it is carried.
        """
        project = _indexed(
            tmp_path,
            {
                "pkg/__init__.py": '"""pkg."""\n',
                "pkg/lib.py": (
                    '"""lib."""\n'
                    "from typing import (\n"
                    "    Any,\n"
                    "    Iterator,\n"
                    ")\n"
                    "\n\n"
                    "def helper(value: Any) -> int:\n"
                    "    return 1\n"
                ),
                "app.py": "from pkg.lib import helper\n\n\ndef run():\n    return helper(1)\n",
            },
        )

        code, _ = _cli(project, ["move-symbol", "pkg.lib:helper", "pkg.util"])

        assert code == 0
        assert (project / "pkg" / "util.py").read_text() == (
            '"""pkg.util."""\n'
            "\n"
            "from typing import Any\n"
            "\n\n"
            "def helper(value: Any) -> int:\n"
            "    return 1\n"
        )

    def test_a_guarded_import_the_destination_already_binds_does_not_refuse(
        self, tmp_path
    ):
        """Quantified over what the move *writes*, not over what the body needs.

        The destination already binds ``Heavy`` to the same origin, so nothing
        is written and no unguarded import is introduced — there is nothing to
        refuse, and refusing here would be over-strict.
        """
        project = _indexed(
            tmp_path,
            {
                "pkg/__init__.py": '"""pkg."""\n',
                "pkg/heavy.py": '"""heavy."""\n\n\nclass Heavy:\n    pass\n',
                "pkg/lib.py": (
                    '"""lib."""\n'
                    "from typing import TYPE_CHECKING\n"
                    "\n"
                    "if TYPE_CHECKING:\n"
                    "    from pkg.heavy import Heavy\n"
                    "\n\n"
                    'def helper(value: "Heavy") -> int:\n'
                    "    return 1\n"
                ),
                "pkg/util.py": (
                    '"""util."""\n'
                    "from pkg.heavy import Heavy\n"
                    "\n\n"
                    "def existing() -> Heavy:\n"
                    "    return Heavy()\n"
                ),
                "app.py": "from pkg.lib import helper\n\n\ndef run():\n    return helper(None)\n",
            },
        )

        code, _ = _cli(project, ["move-symbol", "pkg.lib:helper", "pkg.util"])

        assert code == 0
        text = (project / "pkg" / "util.py").read_text()
        assert text.count("from pkg.heavy import Heavy") == 1
        assert text.endswith('def helper(value: "Heavy") -> int:\n    return 1\n')


_GUARDED_DEST = {
    "pkg/__init__.py": '"""pkg."""\n',
    "pkg/heavy.py": '"""heavy."""\n\n\nclass Heavy:\n    """Heavy."""\n',
    "pkg/lib.py": (
        '"""lib."""\n'
        "from pkg.heavy import Heavy\n"
        "\n\n"
        "def helper():\n"
        '    """Help."""\n'
        "    return Heavy()\n"
    ),
    "app.py": "from pkg.lib import helper\n\n\ndef run():\n    return helper()\n",
}
"""A body needing a *run-time* ``Heavy`` the source binds at the top level."""


class TestGuardedDestinationBinding:
    """A destination binding under its own guard does not count as present.

    ``destination-imports-compatible`` compared local name and
    ``imported_from`` only, and the index records a guarded module-level
    import exactly like a plain one (``if`` opens no scope). So a destination
    whose matching binding sat under ``if TYPE_CHECKING:`` was declared
    compatible, *nothing was written*, and the move exited 0 having produced a
    module whose moved body raises ``NameError`` the moment it runs — the
    mirror of the guard the source-side refusal covers, and the silent half.

    Writing the import for real is not the answer either: it executes at run
    time the very import the destination's guard prevents, which is worse than
    the ``NameError`` whenever the guard was breaking an import cycle (see
    :class:`TestGuardedDestinationCycle`). Both sets of bytes are wrong, so a
    proven-guarded destination binding whose source binding is proven
    top-level refuses by name instead.
    """

    def test_a_type_checking_guarded_destination_binding_refuses_by_name(
        self, tmp_path
    ):
        """Named refusal, and the destination is left exactly as it was."""
        destination = (
            '"""util."""\n'
            "from typing import TYPE_CHECKING\n"
            "\n"
            "if TYPE_CHECKING:\n"
            "    from pkg.heavy import Heavy\n"
            "\n\n"
            'def existing(h: "Heavy"):\n'
            '    """Existing."""\n'
            "    return h\n"
        )
        files = {**_GUARDED_DEST, "pkg/util.py": destination}

        error = _refuse(tmp_path, files, "pkg.lib:helper", "pkg.util")

        assert error.code == "plan-refused"
        assert error.precondition == "destination-imports-compatible"
        assert "'Heavy'" in error.detail
        assert "binds that name under 'if TYPE_CHECKING:'" in error.detail
        assert "not bound there at run time" in error.detail
        assert "Promote the guard in 'pkg.util' first" in error.detail

    def test_the_refusal_leaves_no_bytes_and_no_transaction(self, tmp_path):
        """A refusal at plan time is not a rolled-back apply — nothing happened."""
        destination = (
            '"""util."""\n'
            "from typing import TYPE_CHECKING\n"
            "\n"
            "if TYPE_CHECKING:\n"
            "    from pkg.heavy import Heavy\n"
            "\n\n"
            'def existing(h: "Heavy"):\n'
            '    """Existing."""\n'
            "    return h\n"
        )
        project = _indexed(
            tmp_path, {**_GUARDED_DEST, "pkg/util.py": destination}
        )
        before = _snapshot(project)

        code, payload = _cli(project, ["move-symbol", "pkg.lib:helper", "pkg.util"])

        assert code == 1
        assert payload["code"] == "plan-refused"
        assert _snapshot(project) == before
        # Not even a duplicate binding (ruff F811) reached the destination.
        assert (project / "pkg" / "util.py").read_text().count(
            "from pkg.heavy import Heavy"
        ) == 1
        code, listing = _cli(project, ["transactions", "list"])
        assert code == 0
        assert listing == []

    def test_a_guarded_destination_binding_of_another_origin_still_refuses(
        self, tmp_path
    ):
        """Counting a guarded match absent does not loosen the collision refusal.

        The destination's ``Heavy`` is a *different* ``Heavy``. Writing a
        second binding of the name would shadow one of them silently, so this
        stays the named refusal it has always been rather than becoming a
        write.
        """
        files = {
            **_GUARDED_DEST,
            "pkg/light.py": '"""light."""\n\n\nclass Heavy:\n    """Other."""\n',
            "pkg/util.py": (
                '"""util."""\n'
                "from typing import TYPE_CHECKING\n"
                "\n"
                "if TYPE_CHECKING:\n"
                "    from pkg.light import Heavy\n"
                "\n\n"
                'def existing(h: "Heavy"):\n'
                '    """Existing."""\n'
                "    return h\n"
            ),
        }

        error = _refuse(tmp_path, files, "pkg.lib:helper", "pkg.util")

        assert error.code == "plan-refused"
        assert error.precondition == "destination-imports-compatible"
        assert "'Heavy'" in error.detail
        assert "already binds that name as" in error.detail

    def test_guarded_on_both_sides_reaches_the_source_side_refusal(self, tmp_path):
        """The other branch counting a guarded match absent opens.

        Nothing can be written here — the source's own binding is guarded too —
        so routing the name into the carried set hands it to
        ``carried-imports-unconditional``. This is a tightening: it used to
        exit 0, write nothing, and leave a body referring to a type-only name.
        """
        files = {
            "pkg/__init__.py": '"""pkg."""\n',
            "pkg/heavy.py": '"""heavy."""\n\n\nclass Heavy:\n    """Heavy."""\n',
            "pkg/lib.py": (
                '"""lib."""\n'
                "from typing import TYPE_CHECKING\n"
                "\n"
                "if TYPE_CHECKING:\n"
                "    from pkg.heavy import Heavy\n"
                "\n\n"
                "def helper():\n"
                '    """Help."""\n'
                "    return Heavy()\n"
            ),
            "pkg/util.py": (
                '"""util."""\n'
                "from typing import TYPE_CHECKING\n"
                "\n"
                "if TYPE_CHECKING:\n"
                "    from pkg.heavy import Heavy\n"
                "\n\n"
                'def existing(h: "Heavy"):\n'
                '    """Existing."""\n'
                "    return h\n"
            ),
            "app.py": "from pkg.lib import helper\n\n\ndef run():\n    return helper()\n",
        }

        error = _refuse(tmp_path, files, "pkg.lib:helper", "pkg.util")

        assert error.code == "plan-refused"
        assert error.precondition == "carried-imports-unconditional"
        assert "if TYPE_CHECKING:" in error.detail
        assert "pkg/lib.py:5" in error.detail


class TestGuardedDestinationCycle:
    """Why a guarded destination binding refuses instead of being written.

    The destination guards its import of ``pkg.cycle`` because ``pkg.cycle``
    imports *it* — the ordinary reason a module guards an import at all.
    Writing the carried import as a plain module-level statement beside that
    guard executes the back edge at import time, so ``import pkg.util`` starts
    raising ``ImportError`` and every module importing it breaks, including
    code the move never named. That is strictly worse than the ``NameError``
    the do-nothing branch produced, and there is no third set of bytes that is
    right — hence the refusal.
    """

    _FILES = {
        "pkg/__init__.py": '"""pkg."""\n',
        "pkg/cycle.py": (
            '"""cycle."""\n'
            "from pkg.util import other\n"
            "\n\n"
            "class Thing:\n"
            '    """Thing."""\n'
            "\n\n"
            "def use():\n"
            '    """Use."""\n'
            "    return other()\n"
        ),
        "pkg/util.py": (
            '"""util."""\n'
            "from typing import TYPE_CHECKING\n"
            "\n"
            "if TYPE_CHECKING:\n"
            "    from pkg.cycle import Thing\n"
            "\n\n"
            'def other() -> "Thing | None":\n'
            '    """Other."""\n'
            "    return None\n"
        ),
        "pkg/lib.py": (
            '"""lib."""\n'
            "from pkg.cycle import Thing\n"
            "\n\n"
            "def helper():\n"
            '    """Help."""\n'
            "    return Thing()\n"
        ),
        "app.py": "from pkg.lib import helper\n\n\ndef run():\n    return helper()\n",
    }

    def _imports_cleanly(self, project) -> tuple[int, str]:
        import subprocess
        import sys

        proof = subprocess.run(
            [sys.executable, "-c", "import pkg.util"],
            cwd=project,
            capture_output=True,
            text=True,
        )
        return proof.returncode, proof.stderr

    def test_the_destination_still_imports_after_the_refusal(self, tmp_path):
        """The property the refusal protects, asserted by running Python."""
        project = _indexed(tmp_path, dict(self._FILES))
        before = _snapshot(project)
        assert self._imports_cleanly(project)[0] == 0

        code, payload = _cli(project, ["move-symbol", "pkg.lib:helper", "pkg.util"])

        assert code == 1
        assert payload["code"] == "plan-refused"
        assert "an import cycle it was breaking would then fail" in payload["error"]
        assert _snapshot(project) == before

        returncode, stderr = self._imports_cleanly(project)
        assert returncode == 0, stderr

    def test_writing_the_import_anyway_is_what_the_refusal_costs(self, tmp_path):
        """The counterfactual, pinned: those bytes really do break the module.

        Written out by hand exactly as the move would have emitted them —
        a plain ``from pkg.cycle import Thing`` at the end of the header run,
        above the surviving guard. If this ever stops failing, the refusal has
        become over-strict and this test is the one that says so.
        """
        project = _indexed(tmp_path, dict(self._FILES))
        (project / "pkg" / "util.py").write_text(
            '"""util."""\n'
            "from typing import TYPE_CHECKING\n"
            "from pkg.cycle import Thing\n"
            "\n"
            "if TYPE_CHECKING:\n"
            "    from pkg.cycle import Thing\n"
            "\n\n"
            'def other() -> "Thing | None":\n'
            '    """Other."""\n'
            "    return None\n"
            "\n\n"
            "def helper():\n"
            '    """Help."""\n'
            "    return Thing()\n"
        )

        returncode, stderr = self._imports_cleanly(project)

        assert returncode != 0
        assert "ImportError" in stderr
        assert "partially initialized module 'pkg.util'" in stderr


class TestGuardEvidenceIsScopedToTheStatement:
    """Guard evidence is read per statement, so a broken file elsewhere is not a pass.

    ``carried-imports-unconditional`` used to return early on
    ``root.has_error`` and carry the guard away unguarded, justified by the
    claim that a broken source had already failed ``moved-body-closed``. It
    had not: that check parses only the moved definition's own span, so a
    parse error anywhere outside the body never reaches it. The trap is that
    ``has_error`` is not a proxy for "invalid Python" either —
    tree-sitter-python rejects a PEP 696 type-parameter default that CPython
    3.14 accepts — so the verdict is scoped to the module-level statement the
    binding lands in: a readable guard elsewhere in an unreadable file still
    refuses, and a readable plain import in one still moves.
    """

    _OTHER = '"""other."""\n\n\nclass Other:\n    """Other."""\n'

    def _guarded_source(self, tail: str) -> dict[str, str]:
        return {
            "pkg/__init__.py": '"""pkg."""\n',
            "pkg/other.py": self._OTHER,
            "pkg/lib.py": (
                '"""lib."""\n'
                "from typing import TYPE_CHECKING\n"
                "\n"
                "if TYPE_CHECKING:\n"
                "    from pkg.other import Other\n"
                "\n\n" + tail + "\n"
                "def helper():\n"
                '    """Help."""\n'
                "    return Other\n"
            ),
            "app.py": "from pkg.lib import helper\n\n\ndef run():\n    return helper()\n",
        }

    def test_a_pep_696_default_elsewhere_does_not_excuse_the_guard(self, tmp_path):
        """A file CPython parses and tree-sitter does not still refuses."""
        files = self._guarded_source(
            "def identity[T = int](x: T) -> T:\n    return x\n\n"
        )

        error = _refuse(tmp_path, files, "pkg.lib:helper", "pkg.util")

        assert error.code == "plan-refused"
        assert error.precondition == "carried-imports-unconditional"
        assert "if TYPE_CHECKING:" in error.detail
        assert "pkg/lib.py:5" in error.detail

    def test_a_syntax_error_elsewhere_does_not_excuse_the_guard(self, tmp_path):
        """Neither does a file nothing parses."""
        files = self._guarded_source("def broken(:\n    pass\n\n")

        error = _refuse(tmp_path, files, "pkg.lib:helper", "pkg.util")

        assert error.code == "plan-refused"
        assert error.precondition == "carried-imports-unconditional"
        assert "if TYPE_CHECKING:" in error.detail

    def test_an_unreadable_region_around_the_binding_refuses_by_its_own_name(
        self, tmp_path
    ):
        """No guard proven, no plain binding proven — so no guess, and no write.

        The error sits inside the very block the binding is in, so the CST
        can place it neither at the top level nor under the guard. That is a
        third answer, and it gets its own wording rather than borrowing the
        guard message's.
        """
        files = {
            "pkg/__init__.py": '"""pkg."""\n',
            "pkg/other.py": self._OTHER,
            "pkg/lib.py": (
                '"""lib."""\n'
                "from typing import TYPE_CHECKING\n"
                "\n"
                "if TYPE_CHECKING:\n"
                "    from pkg.other import Other\n"
                "\n"
                "    def broken(:\n"
                "        pass\n"
                "\n\n"
                "def helper():\n"
                '    """Help."""\n'
                "    return Other\n"
            ),
            "app.py": "from pkg.lib import helper\n\n\ndef run():\n    return helper()\n",
        }

        error = _refuse(tmp_path, files, "pkg.lib:helper", "pkg.util")

        assert error.code == "plan-refused"
        assert error.precondition == "carried-imports-unconditional"
        assert "does not parse well enough to tell" in error.detail
        assert "will not do so on a guess" in error.detail
        assert "if TYPE_CHECKING:" not in error.detail.split("guard such as")[0]

    def test_a_plain_import_in_an_unreadable_file_still_moves(self, tmp_path):
        """The over-strict direction the scoping exists to avoid.

        Refusing on ``root.has_error`` would refuse every valid 3.14 file
        tree-sitter cannot read. The binding here is proven top-level, so the
        move proceeds and writes it.
        """
        project = _indexed(
            tmp_path,
            {
                "pkg/__init__.py": '"""pkg."""\n',
                "pkg/other.py": self._OTHER,
                "pkg/lib.py": (
                    '"""lib."""\n'
                    "from pkg.other import Other\n"
                    "\n\n"
                    "def identity[T = int](x: T) -> T:\n"
                    "    return x\n"
                    "\n\n"
                    "def helper():\n"
                    '    """Help."""\n'
                    "    return Other\n"
                ),
                "app.py": "from pkg.lib import helper\n\n\ndef run():\n    return helper()\n",
            },
        )

        code, _ = _cli(project, ["move-symbol", "pkg.lib:helper", "pkg.util"])

        assert code == 0
        assert (project / "pkg" / "util.py").read_text() == (
            '"""pkg.util."""\n'
            "\n"
            "from pkg.other import Other\n"
            "\n\n"
            "def helper():\n"
            '    """Help."""\n'
            "    return Other\n"
        )


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
# The directories a destination is born into
# ---------------------------------------------------------------------------


class TestDestinationDirectoryLifecycle:
    """Rollback undoes the destination's *directories*, not only its file.

    An empty directory left behind is not inert: under PEP 420 it is an
    importable namespace package, so a rollback that removed ``util.py`` and
    kept ``pkg/deep/inner/`` would leave ``import pkg.deep.inner`` succeeding
    where it failed before the move — the tree would not be restored. The
    mechanism is the ``TransactionHeader.created_dirs`` set apply records and
    ``TransactionApplier._remove_dirs`` consumes on rollback, and these two
    tests pin both of its edges: what the recorded set removes, and the
    pre-existing directory it must now spare.
    """

    def test_rollback_removes_the_conjured_directories_and_spares_the_ancestor(
        self, tmp_path
    ):
        """Only the segments the move conjured go; ``pkg/`` predates it and stays."""
        project = _indexed(tmp_path, dict(_LIB))
        before = _snapshot(project)

        code, payload = _cli(
            project, ["move-symbol", "pkg.lib:helper", "pkg.deep.inner.util"]
        )

        assert code == 0
        assert (project / "pkg" / "deep" / "inner" / "util.py").is_file()

        code, _ = _cli(project, ["rollback", payload["tx_id"]])

        assert code == 0
        assert not (project / "pkg" / "deep").exists()
        assert (project / "pkg").is_dir()
        assert (project / "pkg" / "__init__.py").is_file()
        assert _snapshot(project) == before

    def test_rollback_spares_an_ancestor_that_was_already_there_but_empty(
        self, tmp_path
    ):
        """Reversed: a directory that predates the move is no longer pruned.

        Apply now persists the conjured directory list on the transaction
        header (``TransactionHeader.created_dirs``), so a directory that
        existed but was empty before the move is no longer indistinguishable
        from one the move created — rollback reads the recorded set and
        leaves anything not in it alone, this pre-existing empty ``pkg/sub``
        included.
        """
        project = _indexed(tmp_path, dict(_LIB))
        (project / "pkg" / "sub").mkdir()

        code, payload = _cli(project, ["move-symbol", "pkg.lib:helper", "pkg.sub.util"])
        assert code == 0
        assert (project / "pkg" / "sub" / "util.py").is_file()

        code, _ = _cli(project, ["rollback", payload["tx_id"]])

        assert code == 0
        assert (project / "pkg" / "sub").is_dir()
        assert list((project / "pkg" / "sub").iterdir()) == []
        assert (project / "pkg").is_dir()


# ---------------------------------------------------------------------------
# The unused-import debt a move creates in the source module
# ---------------------------------------------------------------------------


class TestSourceImportDebt:
    """A move leaves the source's now-unused imports alone, on purpose.

    Deleting a definition out of a module can strand the imports only that
    definition used. ``delete-symbol`` has the identical property, and this
    project's answer to that class of debt is not "every planner cleans up
    after itself" — it is the ``unused-imports`` rule plus ``check --fix``,
    the same composition ``check --fix --fix-until-clean`` exists to iterate.
    The planner deliberately does not compose ``RemoveImportPlanner``: that
    would delete lines the user never named, and an import with no reference
    is not always dead (side-effect imports, re-export-by-import).

    This test pins all three halves of the accepted behavior — the move leaves
    it, ``check`` reports it, ``check --fix`` removes exactly it.
    """

    _FILES = {
        "src/pkg/__init__.py": '"""pkg."""\n',
        "src/pkg/lib.py": (
            '"""lib."""\n'
            "import os\n"
            "import sys\n"
            "\n\n"
            "def helper(value):\n"
            "    return os.sep + value\n"
            "\n\n"
            "def stay():\n"
            "    return sys.platform\n"
        ),
        "src/app.py": (
            "from pkg.lib import helper\n\n\ndef run():\n    return helper('a')\n"
        ),
    }
    _CONFIG = '\n[tool.pypeeker]\nsrc = ["src"]\nrules = ["unused-imports"]\n'

    def test_an_import_only_the_moved_body_used_is_left_for_the_unused_imports_rule(
        self, tmp_path
    ):
        project = _indexed(tmp_path, dict(self._FILES), src_roots=self._CONFIG)

        code, _ = _cli(project, ["move-symbol", "pkg.lib:helper", "pkg.util"])
        assert code == 0

        source = (project / "src" / "pkg" / "lib.py").read_text()
        assert source == (
            '"""lib."""\nimport os\nimport sys\n\n\ndef stay():\n    return sys.platform\n'
        )

        # ``check`` prints text, not JSON, so it cannot go through ``_cli``.
        os.chdir(project)
        reported = CliRunner().invoke(main, ["check"], catch_exceptions=False)
        assert reported.exit_code == 1
        assert (
            "src/pkg/lib.py:2: [unused-imports] import 'os' is unused in this module"
            in reported.output
        )

        fixed = CliRunner().invoke(main, ["check", "--fix"], catch_exceptions=False)
        assert fixed.exit_code == 0
        assert json.loads(fixed.output)["fixes"] == [
            {
                "fix_id": "unused-imports:remove:pkg.lib:os",
                "description": "remove the unused import 'os'",
                "violation": (
                    "src/pkg/lib.py:2: [unused-imports] "
                    "import 'os' is unused in this module"
                ),
            }
        ]
        assert (project / "src" / "pkg" / "lib.py").read_text() == (
            '"""lib."""\nimport sys\n\n\ndef stay():\n    return sys.platform\n'
        )


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

    def test_the_batch_command_documents_the_move_symbol_kind(self):
        """``batch`` accepts the kind, so its help has to say so.

        ``build_batch_intents`` has always dispatched ``"move-symbol"`` and
        listed it in its own ValueError, but the command's help enumerated the
        other five kinds only — a user reading ``--help`` to learn what a batch
        can contain was told, wrongly, that a move could not be in one.
        """
        result = CliRunner().invoke(main, ["batch", "--help"])

        assert result.exit_code == 0
        # Click rewraps help text and breaks it on hyphens, so compare with
        # every run of whitespace removed rather than against a literal line.
        squashed = "".join(result.output.split())
        assert '"extract-method"|"move-symbol"|"fix"' in squashed


# ---------------------------------------------------------------------------
# TASK-141: move-symbol's two raw-decode sites
# ---------------------------------------------------------------------------


def _refuse_with_bytes(
    tmp_path, files: dict[str, str], raw: dict[str, bytes], symbol_id, dest_module
) -> SubmitError:
    """Like ``_refuse``, but plants non-UTF-8 bytes for ``raw`` before indexing.

    ``_project`` writes text, so a non-UTF-8 fixture has to be planted
    separately; ``index`` itself handles undecodable comment bytes fine (the
    binder only decodes identifier and docstring nodes), so the real command
    still does the indexing and the planner sees exactly what the CLI would.
    """
    from pypeeker.storage import IndexStore

    project = _project(tmp_path, files)
    for name, content in raw.items():
        (project / name).write_bytes(content)
    code, _ = _cli(project, ["index", str(project)])
    assert code == 0
    store = IndexStore(project)
    intent = MoveSymbolIntent("move-symbol", symbol_id, dest_module)
    with pytest.raises(SubmitError) as excinfo:
        submit_intent(intent, store, TransactionStore(project))
    return excinfo.value


class TestMoveNonUtf8Spans:
    """A move records the definition's text and the destination's text as ``str``.

    Both used to be decoded raw, so a latin-1 comment in either crashed
    ``move-symbol`` with a bare ``UnicodeDecodeError``. Now both refuse
    through the standard envelope. The definition span is guarded from
    *inside* ``MovedBodyClosed`` (which computes that span), so its refusal is
    reported under that precondition's name rather than ``source-is-utf8``
    while the wording stays uniform with the rest of the family.
    """

    def test_non_utf8_definition_refuses_under_moved_body_closed(self, tmp_path):
        error = _refuse_with_bytes(
            tmp_path,
            {
                "pkg/__init__.py": '"""pkg."""\n',
                "pkg/lib.py": '"""lib."""\n\n\ndef helper():  # note\n    return 1\n',
            },
            {
                "pkg/lib.py": (
                    b'"""lib."""\n\n\ndef helper():  # caf\xe9\n    return 1\n'
                )
            },
            "pkg.lib:helper",
            "pkg.util",
        )

        assert error.code == "plan-refused"
        assert error.precondition == "moved-body-closed"
        assert error.detail == (
            "File is not valid UTF-8: pkg/lib.py "
            "(byte 33: invalid continuation byte)"
        )

    def test_non_utf8_destination_refuses_under_source_is_utf8(self, tmp_path):
        error = _refuse_with_bytes(
            tmp_path,
            {
                "pkg/__init__.py": '"""pkg."""\n',
                "pkg/lib.py": '"""lib."""\n\n\ndef helper():\n    return 1\n',
                "pkg/util.py": '"""util."""\n\nVALUE = 1  # note\n',
            },
            {"pkg/util.py": b'"""util."""\n\nVALUE = 1  # caf\xe9\n'},
            "pkg.lib:helper",
            "pkg.util",
        )

        assert error.code == "plan-refused"
        assert error.precondition == "source-is-utf8"
        assert error.detail == (
            "File is not valid UTF-8: pkg/util.py "
            "(byte 29: invalid continuation byte)"
        )

    def test_non_utf8_importer_segment_refuses_under_source_is_utf8(self, tmp_path):
        """A move records importer bytes too, and they were the unguarded site.

        The sweep first classified the comma-separated import segment as
        structurally safe: ``_import_name_segments`` derives a segment's
        ``bound_name`` from the slice it records, so a trailing comment
        would land inside the slice and break the match. That holds only
        for the plain ``from m import x`` form. The aliased form takes a
        *substring* — ``rsplit(b" as ", 1)[1]`` — so a comment ending in
        ``as h`` matches on ``h`` while sitting inside the recorded span,
        and ``_importer_edits`` decoded it raw.
        """
        error = _refuse_with_bytes(
            tmp_path,
            {
                "pkg/__init__.py": '"""pkg."""\n',
                "pkg/lib.py": (
                    '"""lib."""\n\n\ndef alpha():\n    return 1\n'
                    "\n\ndef helper():\n    return 2\n"
                ),
                "pkg/user.py": (
                    '"""user."""\n\nfrom pkg.lib import alpha, helper as h  # note\n'
                    "\n\ndef use():\n    return alpha() + h()\n"
                ),
            },
            {
                "pkg/user.py": (
                    b'"""user."""\n\nfrom pkg.lib import alpha, helper as h'
                    b"  # caf\xe9 as h\n\n\ndef use():\n    return alpha() + h()\n"
                )
            },
            "pkg.lib:helper",
            "pkg.util",
        )

        assert error.code == "plan-refused"
        assert error.precondition == "source-is-utf8"
        assert error.detail == (
            "File is not valid UTF-8: pkg/user.py "
            "(byte 58: invalid continuation byte)"
        )

    def test_ascii_importer_segment_beside_undecodable_comment_still_moves(
        self, tmp_path
    ):
        """The importer guard is span-scoped, so it must not refuse this.

        Same line shape, but the moved symbol is the *first* entry: both
        recorded spans (the entry, and the deletion span running up to the
        next entry's start) are pure ASCII, and the latin-1 comment sits
        outside them. A whole-file or whole-line guard here would refuse a
        rewrite that works — the failure mode TASK-136's and TASK-139's
        probes established.
        """
        project = _project(
            tmp_path,
            {
                "pkg/__init__.py": '"""pkg."""\n',
                "pkg/lib.py": (
                    '"""lib."""\n\n\ndef alpha():\n    return 1\n'
                    "\n\ndef helper():\n    return 2\n"
                ),
                "pkg/user.py": (
                    '"""user."""\n\nfrom pkg.lib import helper, alpha  # note\n'
                    "\n\ndef use():\n    return alpha() + helper()\n"
                ),
            },
        )
        (project / "pkg/user.py").write_bytes(
            b'"""user."""\n\nfrom pkg.lib import helper, alpha  # caf\xe9\n'
            b"\n\ndef use():\n    return alpha() + helper()\n"
        )
        code, _ = _cli(project, ["index", str(project)])
        assert code == 0

        code, payload = _cli(
            project, ["move-symbol", "pkg.lib:helper", "pkg.util"]
        )

        assert code == 0, payload
        assert (project / "pkg/user.py").read_bytes() == (
            b'"""user."""\n\nfrom pkg.util import helper\n'
            b"from pkg.lib import alpha  # caf\xe9\n"
            b"\n\ndef use():\n    return alpha() + helper()\n"
        )
