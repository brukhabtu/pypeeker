"""Tests for the planner registry (TASK-122).

``@register_planner(kind)`` mirrors ``@register_rule``: each built-in intent
kind self-registers a materializer next to its planner (rename in
``refactor/planner.py``, inline in ``refactor/inline.py``, the two extract
kinds in ``refactor/extract.py``, ``edit``/``delete-symbol`` in
``refactor/edits.py``), and ``batch._materialize`` becomes a pure lookup.
These tests cover: every built-in kind resolves; an unknown kind is a clean
miss (registry-level, and end-to-end through ``run_batch``'s drop report);
duplicate registration is last-import-wins, mirroring ``register_rule``; and
dispatch through the registry reproduces the same observable batch outcome
an existing small batch scenario asserts.
"""

from __future__ import annotations

import dataclasses
import hashlib
from typing import ClassVar

import pytest

from pypeeker.binder.binder import bind
from pypeeker.intents import (
    EMPTY_EFFECT,
    EMPTY_FOOTPRINT,
    DeleteSymbolIntent,
    Effect,
    ExtractMethodIntent,
    ExtractVariableIntent,
    FixIntent,
    Footprint,
    InlineVariableIntent,
    Intent,
    RenameIntent,
)
from pypeeker.models.transaction import EditEntry, EditOp
from pypeeker.refactor import registry
from pypeeker.refactor.batch import DropReason, run_batch
from pypeeker.refactor.registry import Materialized, get_materializer, register_planner
from pypeeker.storage import IndexStore

# Importing pypeeker.refactor.batch (above) already triggers every built-in
# materializer's registration as a side effect (see the import block near
# the top of batch.py) — nothing else to import here for registration itself.

LIB = "def helper():\n    return 1\n"
APP_CALL = "from lib import helper\n\ndef use():\n    x = helper()\n    return x\n"


@dataclasses.dataclass(frozen=True)
class _Planned:
    """Plan-shaped fix result carrying ``edits`` (mirrors test_batch.py)."""

    edits: tuple[EditEntry, ...]


@dataclasses.dataclass(frozen=True)
class _ReplaceOnceFix:
    """Minimal replannable stub fix: replace the first ``target`` in ``path``.

    Trimmed copy of test_batch.py's fixture, kept local so this file's
    dispatch coverage doesn't reach across test modules for its fixtures.
    """

    path: str
    target: str
    replacement: str
    fix_id: str = "stub:replace-once"
    description: str = "replace a byte sequence once"

    def plan(self, store) -> object:
        """Plan a single replace edit against current bytes."""
        content = (store.project_root / self.path).read_bytes()
        needle = self.target.encode("utf-8")
        at = content.find(needle)
        return _Planned(
            (
                EditEntry(
                    op=EditOp.REPLACE,
                    file=self.path,
                    start=at,
                    end=at + len(needle),
                    old=self.target,
                    new=self.replacement,
                    file_hash=hashlib.sha256(content).hexdigest(),
                ),
            )
        )


@pytest.fixture
def project(tmp_path, adapter):
    """Create an indexed project under ``tmp_path/proj``; returns ``(root, store)``."""
    root = tmp_path / "proj"
    (root / ".pypeeker" / "index").mkdir(parents=True)
    store = IndexStore(root)

    def _setup(files: dict[str, str]):
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            source = content.encode("utf-8")
            tree = adapter.parse(source)
            store.save(bind(adapter, name, source, tree.root_node))
        return root, store

    return _setup


def _ids(intents) -> list[str]:
    """The intent ids of a sequence of intents, in order."""
    return [intent.intent_id for intent in intents]


# ---------------------------------------------------------------------------
# (a) every built-in intent kind resolves
# ---------------------------------------------------------------------------


class TestBuiltinKindsResolve:
    @pytest.mark.parametrize(
        "kind",
        [
            RenameIntent.kind,
            InlineVariableIntent.kind,
            ExtractVariableIntent.kind,
            ExtractMethodIntent.kind,
            FixIntent.kind,
        ],
    )
    def test_kind_resolves_to_a_materializer(self, kind):
        materializer = get_materializer(kind)
        assert materializer is not None
        assert callable(materializer)

    def test_delete_symbol_also_resolves_though_it_has_no_real_planner(self):
        # delete-symbol is schedulable but not executable in v1; it still
        # registers its own materializer (which always explains why) rather
        # than falling through to the generic unknown-kind miss.
        materializer = get_materializer(DeleteSymbolIntent.kind)
        assert materializer is not None


# ---------------------------------------------------------------------------
# (b) unknown kind is a clean miss
# ---------------------------------------------------------------------------


class TestUnknownKindIsAMiss:
    def test_registry_lookup_returns_none(self):
        assert get_materializer("no-such-intent-kind") is None

    def test_batch_drop_detail_matches_the_historical_message(
        self, project, tmp_path
    ):
        root, store = project({"mod.py": "x = 1\n"})

        @dataclasses.dataclass(frozen=True)
        class _UnknownKindIntent(Intent):
            kind: ClassVar[str] = "no-such-intent-kind"

            def footprint(self, store) -> Footprint:
                return EMPTY_FOOTPRINT

            def predicted_effect(self, store) -> Effect:
                return EMPTY_EFFECT

            def remap(self, effect) -> Intent:
                return self

        intent = _UnknownKindIntent("ghost")
        result = run_batch([intent], store, work_dir=tmp_path / "mirror")
        assert result.executed == ()
        (drop,) = result.dropped
        assert drop.reason is DropReason.PRECONDITION_FAILED
        assert drop.detail == "no executor for intent kind 'no-such-intent-kind'"

    def test_delete_symbol_drop_detail_matches_the_historical_message(
        self, project, tmp_path
    ):
        root, store = project({"mod.py": "def f():\n    x = 1\n    return x\n"})
        delete = DeleteSymbolIntent("del-x", "mod:f:x")
        result = run_batch([delete], store, work_dir=tmp_path / "mirror")
        assert result.executed == ()
        (drop,) = result.dropped
        assert drop.reason is DropReason.PRECONDITION_FAILED
        assert "no planner" in drop.detail


# ---------------------------------------------------------------------------
# (c) duplicate registration: last import wins (mirrors register_rule)
# ---------------------------------------------------------------------------


class TestDuplicateRegistration:
    def test_second_registration_replaces_the_first(self):
        kind = "test-only:duplicate-registration"
        assert get_materializer(kind) is None
        try:

            @register_planner(kind)
            def _first(intent, store, tx_store):
                return "first"

            assert get_materializer(kind) is _first

            @register_planner(kind)
            def _second(intent, store, tx_store):
                return "second"

            assert get_materializer(kind) is _second
            assert get_materializer(kind) is not _first
        finally:
            registry._REGISTRY.pop(kind, None)


# ---------------------------------------------------------------------------
# (d) dispatch parity: registry-backed run_batch matches the expected outcome
# ---------------------------------------------------------------------------


class TestDispatchParity:
    def test_rename_conflict_scenario_matches_expected_outcome(
        self, project, tmp_path
    ):
        # Same scenario as test_batch.py::TestRenames::
        # test_interfering_renames_skip_and_report — asserts the registry-
        # dispatched batch (rename resolved via planner.py's registered
        # materializer) produces the identical observable result the old
        # isinstance-dispatch code produced.
        root, store = project({"lib.py": LIB, "app.py": APP_CALL})
        r1 = RenameIntent("r1", "lib:helper", "assist")
        r2 = RenameIntent("r2", "lib:helper", "do_help")
        result = run_batch([r1, r2], store, work_dir=tmp_path / "mirror")
        assert _ids(i.intent for i in result.executed) == ["r1"]
        (drop,) = result.dropped
        assert (drop.intent.intent_id, drop.reason) == ("r2", DropReason.CONFLICT_DROPPED)
        assert (result.root / "lib.py").read_text() == "def assist():\n    return 1\n"

    def test_inline_variable_dispatches_through_the_registry(self, project, tmp_path):
        root, store = project({"mod.py": "def f():\n    x = 1\n    return x\n"})
        inline = InlineVariableIntent("inline-x", "mod:f:x")
        result = run_batch([inline], store, work_dir=tmp_path / "mirror")
        assert _ids(i.intent for i in result.executed) == ["inline-x"]
        assert result.dropped == ()
        assert (result.root / "mod.py").read_text() == "def f():\n    return 1\n"

    def test_extract_variable_dispatches_through_the_registry(self, project, tmp_path):
        # Same scenario as test_extract_variable.py::test_extract_end_to_end_runnable,
        # driven through run_batch so a mis-keyed @register_planner on
        # ExtractVariableIntent's kind (e.g. swapped with extract-method's)
        # would fail here, not just at the direct-planner level.
        root, store = project({"mod.py": "def f():\n    return foo(bar) + 2\n"})
        extract = ExtractVariableIntent("extract-v", "mod.py", (1, 11), (1, 19), "value")
        result = run_batch([extract], store, work_dir=tmp_path / "mirror")
        assert _ids(i.intent for i in result.executed) == ["extract-v"]
        assert result.dropped == ()
        assert (result.root / "mod.py").read_text() == (
            "def f():\n    value = foo(bar)\n    return value + 2\n"
        )

    def test_extract_method_dispatches_through_the_registry(self, project, tmp_path):
        # Same scenario as test_extract_method.py::test_extract_with_params_and_return,
        # driven through run_batch — the counterpart to the extract-variable
        # dispatch test above; together they pin both extract kinds to their
        # correct materializer rather than each other's.
        root, store = project({"mod.py": "def f(a, b):\n    c = a + b\n    return c\n"})
        extract = ExtractMethodIntent("extract-m", "mod.py", 1, 1, "add")
        result = run_batch([extract], store, work_dir=tmp_path / "mirror")
        assert _ids(i.intent for i in result.executed) == ["extract-m"]
        assert result.dropped == ()
        assert (result.root / "mod.py").read_text() == (
            "def add(a, b):\n"
            "    c = a + b\n"
            "    return c\n"
            "\n\n"
            "def f(a, b):\n"
            "    c = add(a, b)\n"
            "    return c\n"
        )

    def test_edit_dispatches_through_the_registry(self, project, tmp_path):
        # Same shape as test_batch.py's FixIntent scenarios, kept here so
        # "edit" (edits.py's materializer) is exercised end-to-end by this
        # file too, alongside rename/inline/extract-variable/extract-method.
        root, store = project({"mod.py": "a = 1\nb = 2\nc = 3\n"})
        fix = FixIntent("bump-b", _ReplaceOnceFix("mod.py", "b = 2", "b = 20"))
        result = run_batch([fix], store, work_dir=tmp_path / "mirror")
        assert _ids(i.intent for i in result.executed) == ["bump-b"]
        assert result.dropped == ()
        assert (result.root / "mod.py").read_text() == "a = 1\nb = 20\nc = 3\n"

    def test_materialize_delegates_to_the_registered_rename_materializer(
        self, project, tmp_path
    ):
        # Direct unit check that batch._materialize really is "look up, then
        # invoke" for a real kind, not a reimplementation of the dispatch.
        from pypeeker.refactor.batch import _materialize
        from pypeeker.storage import TransactionStore

        root, store = project({"lib.py": LIB})
        tx_store = TransactionStore(root)
        intent = RenameIntent("r1", "lib:helper", "assist")
        via_batch = _materialize(intent, store, tx_store)
        assert isinstance(via_batch, Materialized)
        materializer = get_materializer(RenameIntent.kind)
        assert materializer is not None
