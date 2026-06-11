"""Tests for the convention-rename converter (refactor/convention_renames).

Unit tests cover the pairs -> RenameIntent conversion and every skip reason
(taken targets, deterministic pending-collision drops, override-unsafe
methods, no-op / not-found / duplicate), plus the plan-batch-compatible
intents-file writer. The end-to-end tests drive the full TASK-91 workflow —
naming-conventions findings -> rename_pair -> converter -> run_batch ->
flatten_batch -> TransactionApplier — over tmp projects with cross-module
call sites, a barrel re-export, and a naively-colliding pair.
"""

from __future__ import annotations

import json

import pytest

from pypeeker.binder.binder import bind
from pypeeker.check.builtin.naming_conventions import (
    _naming_conventions as naming_conventions,
    _rename_pair as rename_pair,
)
from pypeeker.refactor.batch import flatten_batch, run_batch
from pypeeker.refactor.convention_renames import (
    INTENT_ID_PREFIX,
    SkippedRename,
    SkipReason,
    convention_rename_intents,
    write_intents_file,
)
from pypeeker.refactor.intents import RenameIntent
from pypeeker.storage import IndexStore


@pytest.fixture
def project(tmp_path, adapter):
    """Create an indexed project under ``tmp_path/proj``.

    Returns a callable ``files -> (project_root, store)``; the sibling
    ``tmp_path/mirror`` stays free for ``run_batch``'s work dir.
    """

    def _setup(files: dict[str, str]):
        root = tmp_path / "proj"
        (root / ".semantic-tool" / "index").mkdir(parents=True)
        store = IndexStore(root)
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            source = content.encode("utf-8")
            tree = adapter.parse(source)
            store.save(bind(adapter, name, source, tree.root_node))
        return root, store

    return _setup


def _pairs_from_rule(store: IndexStore) -> list[tuple[str, str]]:
    """The full detection half: run the rule per file, extract rename pairs."""
    pairs: list[tuple[str, str]] = []
    for path in store.list_indexed_files():
        index = store.load(path)
        assert index is not None
        for violation in naming_conventions(index, {}):
            pair = rename_pair(violation)
            if pair is not None:
                pairs.append(pair)
    return pairs


# ── conversion ──────────────────────────────────────────────────────────────


class TestConversion:
    def test_pairs_become_rename_intents_with_deterministic_ids(self, project):
        _, store = project({"mod.py": "def getValue():\n    return 1\n"})
        intents, skipped = convention_rename_intents(
            store, [("mod:getValue", "get_value")]
        )
        assert skipped == []
        (intent,) = intents
        assert isinstance(intent, RenameIntent)
        assert intent.intent_id == f"{INTENT_ID_PREFIX}mod:getValue"
        assert intent.symbol_id == "mod:getValue"
        assert intent.new_name == "get_value"
        assert intent.include_exports is False

    def test_include_exports_passes_through_to_every_intent(self, project):
        _, store = project(
            {"mod.py": "def getValue():\n    return 1\n\n\nclass bad_cls:\n    pass\n"}
        )
        intents, _ = convention_rename_intents(
            store,
            [("mod:getValue", "get_value"), ("mod:bad_cls", "BadCls")],
            include_exports=True,
        )
        assert [i.include_exports for i in intents] == [True, True]

    def test_declared_gating_is_left_to_the_planner(self, project):
        # The converter never opts into receiver-based edits: the planner's
        # include_receivers gating (declared-only) stays the single owner.
        _, store = project({"mod.py": "def getValue():\n    return 1\n"})
        intents, _ = convention_rename_intents(store, [("mod:getValue", "get_value")])
        assert intents[0].include_receivers is False


# ── skip reasons ────────────────────────────────────────────────────────────


class TestSkipReasons:
    def test_no_op_when_suggestion_equals_current_name(self, project):
        _, store = project({"mod.py": "def fine():\n    return 1\n"})
        intents, skipped = convention_rename_intents(store, [("mod:fine", "fine")])
        assert intents == []
        assert skipped == [
            SkippedRename(
                "mod:fine",
                "fine",
                SkipReason.NO_OP,
                "'fine' already has the suggested name",
            )
        ]

    def test_symbol_not_found(self, project):
        _, store = project({"mod.py": "x = 1\n"})
        intents, skipped = convention_rename_intents(store, [("mod:ghost", "spook")])
        assert intents == []
        assert skipped[0].reason is SkipReason.SYMBOL_NOT_FOUND

    def test_duplicate_symbol_keeps_the_first_pair(self, project):
        _, store = project({"mod.py": "def getValue():\n    return 1\n"})
        intents, skipped = convention_rename_intents(
            store, [("mod:getValue", "get_value"), ("mod:getValue", "value_of")]
        )
        assert [i.new_name for i in intents] == ["get_value"]
        (skip,) = skipped
        assert skip.reason is SkipReason.DUPLICATE_SYMBOL
        assert skip.new_name == "value_of"

    def test_target_already_taken_in_same_scope(self, project):
        src = "def getValue():\n    return 1\n\n\ndef get_value():\n    return 2\n"
        _, store = project({"mod.py": src})
        intents, skipped = convention_rename_intents(
            store, [("mod:getValue", "get_value")]
        )
        assert intents == []
        (skip,) = skipped
        assert skip.reason is SkipReason.TARGET_EXISTS
        assert "'get_value'" in skip.detail and "'mod'" in skip.detail

    def test_pending_collision_drops_the_later_pair(self, project):
        src = "def getValue():\n    return 1\n\n\ndef get_Value():\n    return 2\n"
        _, store = project({"mod.py": src})
        pairs = [("mod:getValue", "get_value"), ("mod:get_Value", "get_value")]
        intents, skipped = convention_rename_intents(store, pairs)
        assert [i.symbol_id for i in intents] == ["mod:getValue"]
        (skip,) = skipped
        assert skip.reason is SkipReason.PENDING_COLLISION
        assert skip.symbol_id == "mod:get_Value"
        assert f"{INTENT_ID_PREFIX}mod:getValue" in skip.detail

    def test_pending_collision_is_decided_by_submission_order(self, project):
        # Deterministic and order-respecting: the FIRST submitted claimant
        # wins, whichever it is.
        src = "def getValue():\n    return 1\n\n\ndef get_Value():\n    return 2\n"
        _, store = project({"mod.py": src})
        pairs = [("mod:get_Value", "get_value"), ("mod:getValue", "get_value")]
        intents, skipped = convention_rename_intents(store, pairs)
        assert [i.symbol_id for i in intents] == ["mod:get_Value"]
        assert skipped[0].symbol_id == "mod:getValue"

    def test_same_suggestion_in_different_scopes_does_not_collide(self, project):
        src = (
            "class A:\n    def getValue(self):\n        return 1\n\n\n"
            "class B:\n    def getValue(self):\n        return 2\n"
        )
        _, store = project({"mod.py": src})
        intents, skipped = convention_rename_intents(
            store,
            [("mod:A.getValue", "get_value"), ("mod:B.getValue", "get_value")],
        )
        assert skipped == []
        assert len(intents) == 2

    def test_method_with_override_edge_is_skipped(self, project):
        src = (
            "class Base:\n    def getValue(self):\n        return 1\n\n\n"
            "class Sub(Base):\n    def getValue(self):\n        return 2\n"
        )
        _, store = project({"mod.py": src})
        intents, skipped = convention_rename_intents(
            store, [("mod:Sub.getValue", "get_value")]
        )
        assert intents == []
        (skip,) = skipped
        assert skip.reason is SkipReason.OVERRIDE_UNSAFE
        assert "mod:Base.getValue" in skip.detail

    def test_method_with_unknown_hierarchy_is_skipped(self, project):
        src = "class Sub(External):\n    def getValue(self):\n        return 2\n"
        _, store = project({"mod.py": src})
        _, skipped = convention_rename_intents(
            store, [("mod:Sub.getValue", "get_value")]
        )
        assert skipped[0].reason is SkipReason.OVERRIDE_UNSAFE
        assert "cannot be verified" in skipped[0].detail

    def test_method_without_override_edges_is_emitted(self, project):
        src = "class A:\n    def getValue(self):\n        return 1\n"
        _, store = project({"mod.py": src})
        intents, skipped = convention_rename_intents(
            store, [("mod:A.getValue", "get_value")]
        )
        assert skipped == []
        assert [i.symbol_id for i in intents] == ["mod:A.getValue"]


# ── intents file (plan-batch schema) ────────────────────────────────────────


class TestWriteIntentsFile:
    def test_emits_plan_batch_compatible_entries(self, project, tmp_path):
        _, store = project({"mod.py": "def getValue():\n    return 1\n"})
        intents, _ = convention_rename_intents(
            store, [("mod:getValue", "get_value")], include_exports=True
        )
        path = write_intents_file(intents, tmp_path / "intents.json")
        entries = json.loads(path.read_text())
        assert entries == [
            {
                "kind": "rename",
                "id": f"{INTENT_ID_PREFIX}mod:getValue",
                "symbol_id": "mod:getValue",
                "new_name": "get_value",
                "include_exports": True,
            }
        ]

    def test_default_flags_are_omitted(self, project, tmp_path):
        _, store = project({"mod.py": "def getValue():\n    return 1\n"})
        intents, _ = convention_rename_intents(store, [("mod:getValue", "get_value")])
        path = write_intents_file(intents, tmp_path / "intents.json")
        (entry,) = json.loads(path.read_text())
        assert set(entry) == {"kind", "id", "symbol_id", "new_name"}


# ── end-to-end: findings -> intents -> batch -> flatten -> apply ────────────

CORE = "def BadName():\n    return 1\n\n\nclass bad_class:\n    pass\n"
BARREL = "from pkg.core import BadName\n"
APP = "from pkg import BadName\n\n\ndef use():\n    return BadName()\n"
CONSUMER = "from pkg.core import bad_class\n\n\ndef make():\n    return bad_class()\n"


def _apply_flattened(root, store, result):
    """Flatten a batch result and apply it to the real tree (CLI-equivalent)."""
    from pypeeker.refactor.applier import TransactionApplier
    from pypeeker.storage import TransactionStore

    header, edits = flatten_batch(result, store)
    tx_store = TransactionStore(root)
    tx_store.save(header, edits)
    applied = TransactionApplier(store, tx_store).apply(header.tx_id)
    assert applied["status"] == "applied", applied
    return edits


class TestEndToEnd:
    def test_cross_module_renames_update_call_sites_and_barrel(
        self, project, tmp_path
    ):
        root, store = project(
            {
                "pkg/__init__.py": BARREL,
                "pkg/core.py": CORE,
                "app.py": APP,
                "consumer.py": CONSUMER,
            }
        )

        pairs = _pairs_from_rule(store)
        assert sorted(pairs) == [
            ("pkg.core:BadName", "bad_name"),
            ("pkg.core:bad_class", "BadClass"),
        ]

        intents, skipped = convention_rename_intents(
            store, pairs, include_exports=True
        )
        assert skipped == []
        result = run_batch(intents, store, work_dir=tmp_path / "mirror")
        assert result.dropped == ()
        _apply_flattened(root, store, result)

        # Definitions, the barrel re-export, the barrel consumer's import and
        # call site, and the direct importer all moved to conforming names.
        assert (root / "pkg" / "core.py").read_text() == (
            "def bad_name():\n    return 1\n\n\nclass BadClass:\n    pass\n"
        )
        assert (root / "pkg" / "__init__.py").read_text() == (
            "from pkg.core import bad_name\n"
        )
        assert (root / "app.py").read_text() == (
            "from pkg import bad_name\n\n\ndef use():\n    return bad_name()\n"
        )
        assert (root / "consumer.py").read_text() == (
            "from pkg.core import BadClass\n\n\ndef make():\n    return BadClass()\n"
        )

    def test_naively_colliding_pair_drops_exactly_one_with_reason(
        self, project, tmp_path
    ):
        # Two functions whose suggestions collide in the same scope: renaming
        # both would leave two `get_value` defs in one module. The converter
        # drops the later-submitted pair pre-batch with a naming-flavoured
        # reason; the survivor lands end-to-end.
        src = (
            "def getValue():\n    return 1\n\n\n"
            "def get_Value():\n    return 2\n\n\n"
            "def use():\n    return getValue() + get_Value()\n"
        )
        root, store = project({"mod.py": src})

        pairs = _pairs_from_rule(store)
        assert sorted(pairs) == [
            ("mod:getValue", "get_value"),
            ("mod:get_Value", "get_value"),
        ]

        intents, skipped = convention_rename_intents(store, pairs)
        assert len(intents) == 1
        (skip,) = skipped
        assert skip.reason is SkipReason.PENDING_COLLISION

        result = run_batch(intents, store, work_dir=tmp_path / "mirror")
        assert result.dropped == ()
        _apply_flattened(root, store, result)

        text = (root / "mod.py").read_text()
        survivor, dropped = intents[0].symbol_id, skip.symbol_id
        renamed_def, kept_def = (
            ("def get_value():", "def get_Value():")
            if survivor == "mod:getValue"
            else ("def get_value():", "def getValue():")
        )
        assert renamed_def in text
        assert kept_def in text  # the dropped pair's symbol is untouched
        assert dropped.split(":", 1)[1] in text
        assert "return get_value() + " in text

    def test_intents_file_round_trips_through_run_batch(self, project, tmp_path):
        # The written file re-parses into equivalent intents (the plan-batch
        # contract), and those intents land the same batch.
        root, store = project({"mod.py": "def getValue():\n    return getValue\n"})
        intents, _ = convention_rename_intents(store, [("mod:getValue", "get_value")])
        path = write_intents_file(intents, tmp_path / "intents.json")

        rebuilt = [
            RenameIntent(
                entry["id"],
                entry["symbol_id"],
                entry["new_name"],
                include_exports=bool(entry.get("include_exports", False)),
            )
            for entry in json.loads(path.read_text())
        ]
        assert rebuilt == intents
        result = run_batch(rebuilt, store, work_dir=tmp_path / "mirror")
        assert result.dropped == ()
        assert (result.root / "mod.py").read_text() == (
            "def get_value():\n    return get_value\n"
        )
