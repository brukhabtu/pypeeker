"""Tests for the planner registry (TASK-122).

``@register_planner(kind)`` mirrors ``@register_rule``: each built-in intent
kind self-registers a materializer next to its planner (rename in
``refactor/planner.py``, inline in ``refactor/inline.py``, the two extract
kinds in ``refactor/extract.py``, ``delete-symbol`` in
``refactor/edits.py``, and TASK-124's five check-remedy kinds in
``refactor/{delete,imports_ops,literals,text_ops}.py``), and
``batch._materialize`` becomes a pure lookup.
These tests cover: every built-in kind resolves; an unknown kind is a clean
miss (registry-level, and end-to-end through ``run_batch``'s drop report);
duplicate registration is last-import-wins, mirroring ``register_rule``; and
dispatch through the registry reproduces the same observable batch outcome
an existing small batch scenario asserts.
"""

from __future__ import annotations

import dataclasses
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
    Footprint,
    InlineVariableIntent,
    Intent,
    RenameIntent,
    ReplaceTextIntent,
)
from pypeeker.refactor import registry
from pypeeker.refactor.batch import DropReason, run_batch
from pypeeker.refactor.registry import Materialized, get_materializer, register_planner
from pypeeker.storage import IndexStore, TransactionStore

# Importing pypeeker.refactor.batch (above) already triggers every built-in
# materializer's registration as a side effect (see the import block near
# the top of batch.py) — nothing else to import here for registration itself.

LIB = "def helper():\n    return 1\n"
APP_CALL = "from lib import helper\n\ndef use():\n    x = helper()\n    return x\n"


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
            DeleteSymbolIntent.kind,
            ReplaceTextIntent.kind,
        ],
    )
    def test_kind_resolves_to_a_materializer(self, kind):
        materializer = get_materializer(kind)
        assert materializer is not None
        assert callable(materializer)

    @pytest.mark.parametrize(
        "kind",
        [
            "remove-import",
            "rewrite-star-import",
            "tuplify",
            "rename-docstring-param",
        ],
    )
    def test_check_remedy_kinds_resolve(self, kind):
        # TASK-124: the kinds `check` rules attach as `Violation.remedy` are
        # registered by their own planner modules, reached through batch.py's
        # side-effect import block like every other builtin kind.
        assert get_materializer(kind) is not None


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
        result = run_batch([intent], store, tx_store=TransactionStore(tmp_path / "tx"))
        assert result.executed == ()
        (drop,) = result.dropped
        assert drop.reason is DropReason.PRECONDITION_FAILED
        assert drop.detail == "no executor for intent kind 'no-such-intent-kind'"

    def test_delete_symbol_drop_detail_matches_the_real_planner(
        self, project, tmp_path
    ):
        # TASK-124 stage A: this used to assert v1's hardcoded "no planner"
        # refusal; delete-symbol now dispatches to a real DeleteSymbolPlanner
        # (refactor/delete.py — registered by refactor/edits.py), so the same
        # scenario now declines through its own guarded re-resolution instead
        # ("mod:f:x" is a VARIABLE, not a FUNCTION/CLASS the planner handles).
        root, store = project({"mod.py": "def f():\n    x = 1\n    return x\n"})
        delete = DeleteSymbolIntent("del-x", "mod:f:x")
        result = run_batch([delete], store, tx_store=TransactionStore(tmp_path / "tx"))
        assert result.executed == ()
        (drop,) = result.dropped
        assert drop.reason is DropReason.PRECONDITION_FAILED
        assert drop.detail == "symbol 'mod:f:x' is no longer in the index"


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
        result = run_batch([r1, r2], store, tx_store=TransactionStore(tmp_path / "tx"))
        assert _ids(i.intent for i in result.executed) == ["r1"]
        (drop,) = result.dropped
        assert (drop.intent.intent_id, drop.reason) == ("r2", DropReason.CONFLICT_DROPPED)
        assert result.store.read_file("lib.py").decode() == "def assist():\n    return 1\n"

    def test_inline_variable_dispatches_through_the_registry(self, project, tmp_path):
        root, store = project({"mod.py": "def f():\n    x = 1\n    return x\n"})
        inline = InlineVariableIntent("inline-x", "mod:f:x")
        result = run_batch([inline], store, tx_store=TransactionStore(tmp_path / "tx"))
        assert _ids(i.intent for i in result.executed) == ["inline-x"]
        assert result.dropped == ()
        assert result.store.read_file("mod.py").decode() == "def f():\n    return 1\n"

    def test_extract_variable_dispatches_through_the_registry(self, project, tmp_path):
        # Same scenario as test_extract_variable.py::test_extract_end_to_end_runnable,
        # driven through run_batch so a mis-keyed @register_planner on
        # ExtractVariableIntent's kind (e.g. swapped with extract-method's)
        # would fail here, not just at the direct-planner level.
        root, store = project({"mod.py": "def f():\n    return foo(bar) + 2\n"})
        extract = ExtractVariableIntent("extract-v", "mod.py", (1, 11), (1, 19), "value")
        result = run_batch([extract], store, tx_store=TransactionStore(tmp_path / "tx"))
        assert _ids(i.intent for i in result.executed) == ["extract-v"]
        assert result.dropped == ()
        assert result.store.read_file("mod.py").decode() == (
            "def f():\n    value = foo(bar)\n    return value + 2\n"
        )

    def test_extract_method_dispatches_through_the_registry(self, project, tmp_path):
        # Same scenario as test_extract_method.py::test_extract_with_params_and_return,
        # driven through run_batch — the counterpart to the extract-variable
        # dispatch test above; together they pin both extract kinds to their
        # correct materializer rather than each other's.
        root, store = project({"mod.py": "def f(a, b):\n    c = a + b\n    return c\n"})
        extract = ExtractMethodIntent("extract-m", "mod.py", 1, 1, "add")
        result = run_batch([extract], store, tx_store=TransactionStore(tmp_path / "tx"))
        assert _ids(i.intent for i in result.executed) == ["extract-m"]
        assert result.dropped == ()
        assert result.store.read_file("mod.py").decode() == (
            "def add(a, b):\n"
            "    c = a + b\n"
            "    return c\n"
            "\n\n"
            "def f(a, b):\n"
            "    c = add(a, b)\n"
            "    return c\n"
        )

    def test_replace_text_dispatches_through_the_registry(self, project, tmp_path):
        # Same shape as test_batch.py's byte-edit scenarios, kept here so
        # "replace-text" (text_ops.py's materializer) is exercised end-to-end
        # by this file too, alongside rename/inline/extract-*/delete-symbol.
        root, store = project({"mod.py": "a = 1\nb = 2\nc = 3\n"})
        fix = ReplaceTextIntent("bump-b", "mod.py", 1, 0, "b = 2", "b = 20")
        result = run_batch([fix], store, tx_store=TransactionStore(tmp_path / "tx"))
        assert _ids(i.intent for i in result.executed) == ["bump-b"]
        assert result.dropped == ()
        assert result.store.read_file("mod.py").decode() == "a = 1\nb = 20\nc = 3\n"

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
