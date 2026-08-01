"""Tests for the batch scheduler + overlay simulation loop (TASK-88).

Scheduler tests use stub intents with canned footprints/effects (the
scheduler is pure over the intent protocol); simulation tests run real
planner-backed intents on an in-memory overlay over an indexed project and
assert the simulation's final bytes while the real project stays untouched.
"""

from __future__ import annotations

import dataclasses
import hashlib
from typing import ClassVar

import pytest

from pypeeker.binder.binder import bind
from pypeeker.indexer import ensure_fresh
from pypeeker.intents import (
    EMPTY_EFFECT,
    EMPTY_FOOTPRINT,
    DeleteSymbolIntent,
    Effect,
    Footprint,
    InlineVariableIntent,
    Intent,
    RenameIntent,
    ReplaceTextIntent,
)
from pypeeker.models import EditOp
from pypeeker.refactor.batch import (
    BatchAborted,
    BatchPolicy,
    DropReason,
    FlattenError,
    ScheduleCycleError,
    ScheduleError,
    flatten_batch,
    flatten_store,
    run_batch,
    schedule,
)
from pypeeker.refactor.simulate import _rebind as rebind
from pypeeker.storage import IndexStore, OverlayIndexStore, TransactionStore


# ---------------------------------------------------------------------------
# Stubs and fixtures
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _StubIntent(Intent):
    """Scheduler-test intent with a canned footprint and predicted effect."""

    fp: Footprint = EMPTY_FOOTPRINT
    eff: Effect = EMPTY_EFFECT

    kind: ClassVar[str] = "stub"

    def footprint(self, store) -> Footprint:
        """The canned footprint (the store is ignored)."""
        return self.fp

    def predicted_effect(self, store) -> Effect:
        """The canned effect (the store is ignored)."""
        return self.eff

    def remap(self, effect) -> Intent:
        """Identity remap: stub anchors never move."""
        return self


def _replace(intent_id: str, path: str, target: str, replacement: str, **kw):
    """A ``replace-text`` intent: the generic byte-edit intent of these tests.

    Anchored at ``(0, 0)`` so
    :class:`~pypeeker.refactor.text_ops.ReplaceTextPlanner` re-anchors on the
    unique occurrence of ``target`` in the *current* bytes of whatever store
    it is planned over (the real project at schedule time, the simulation
    overlay at execution time) — the replannable behaviour these tests need,
    including declining once the target text is gone.
    """
    return ReplaceTextIntent(intent_id, path, 0, 0, target, replacement, **kw)


def _stub(intent_id: str, *, fp=EMPTY_FOOTPRINT, eff=EMPTY_EFFECT, deps=()) -> _StubIntent:
    """Shorthand stub-intent constructor for scheduler tests."""
    return _StubIntent(intent_id, fp=fp, eff=eff, deps=frozenset(deps))


def _ids(intents) -> list[str]:
    """The intent ids of a sequence of intents, in order."""
    return [intent.intent_id for intent in intents]


@pytest.fixture
def batch_project(tmp_path, adapter):
    """Create an indexed project under ``tmp_path/proj``.

    Returns a callable ``files -> (project_root, store)``; the sibling
    ``tmp_path/tx`` stays free for ``run_batch``'s scratch transaction store.
    """

    def _setup(files: dict[str, str]):
        root = tmp_path / "proj"
        (root / ".pypeeker" / "index").mkdir(parents=True)
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


def _snapshot(root) -> dict[str, bytes]:
    """All file bytes under ``root``, keyed by relative path."""
    return {
        str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }


# ---------------------------------------------------------------------------
# Scheduler: ordering rules
# ---------------------------------------------------------------------------


class TestScheduleOrdering:
    def test_rename_scheduled_after_conflicting_body_edit(self):
        # Tie-break alone would put "a-rename" first; the id-changing rule
        # must override it.
        rename = _stub(
            "a-rename",
            fp=Footprint(writes_symbols={"m:x"}, writes_files={"m.py"}),
            eff=Effect(renamed={"m:x": "m:y"}, files_written={"m.py"}),
        )
        edit = _stub("z-edit", fp=Footprint(writes_files={"m.py"}))
        result = schedule([rename, edit], store=None)
        assert _ids(result.ordered) == ["z-edit", "a-rename"]
        assert result.dropped == ()

    def test_delete_scheduled_after_reader_of_deleted_target(self):
        # Tie-break alone would put "a-delete" first; the delete-after-reader
        # rule must override it.
        delete = _stub(
            "a-delete",
            fp=Footprint(writes_symbols={"m:x"}, writes_files={"a.py"}),
            eff=Effect(deleted={"m:x"}, files_written={"a.py"}),
        )
        reader = _stub(
            "z-reader",
            fp=Footprint(reads_symbols={"m:x"}, writes_files={"z.py"}),
        )
        result = schedule([delete, reader], store=None)
        assert _ids(result.ordered) == ["z-reader", "a-delete"]

    def test_scoped_fact_read_counts_as_reading_the_deleted_target(self):
        delete = _stub(
            "a-delete",
            fp=Footprint(writes_symbols={"m:f"}, writes_files={"a.py"}),
            eff=Effect(deleted={"m:f"}, files_written={"a.py"}),
        )
        reader = _stub(
            "z-reader",
            fp=Footprint(reads_facts={"purity:m:f:x"}, writes_files={"z.py"}),
        )
        result = schedule([delete, reader], store=None)
        assert _ids(result.ordered) == ["z-reader", "a-delete"]

    def test_explicit_deps_are_honored(self):
        first = _stub("z-first")
        second = _stub("a-second", deps={"z-first"})
        result = schedule([second, first], store=None)
        assert _ids(result.ordered) == ["z-first", "a-second"]

    def test_explicit_dep_overrides_conflict_tie_break(self):
        # Tie-break would order "a-late" before "z-early"; the explicit dep
        # must win without manufacturing a cycle.
        early = _stub("z-early", fp=Footprint(writes_files={"m.py"}))
        late = _stub("a-late", fp=Footprint(writes_files={"m.py"}), deps={"z-early"})
        result = schedule([late, early], store=None)
        assert _ids(result.ordered) == ["z-early", "a-late"]

    def test_tie_break_is_input_order_independent(self):
        a = _stub("a", fp=Footprint(writes_files={"m.py"}))
        b = _stub("b", fp=Footprint(writes_files={"m.py"}))
        c = _stub("c", fp=Footprint(writes_files={"a_first.py"}))
        for intents in ([a, b, c], [c, b, a], [b, c, a]):
            result = schedule(intents, store=None)
            assert _ids(result.ordered) == ["c", "a", "b"]


# ---------------------------------------------------------------------------
# Scheduler: cycles and structural errors
# ---------------------------------------------------------------------------


class TestScheduleErrors:
    def test_dependency_cycle_is_a_structured_error(self):
        a = _stub("a", deps={"b"})
        b = _stub("b", deps={"a"})
        with pytest.raises(ScheduleCycleError) as excinfo:
            schedule([a, b], store=None)
        assert set(excinfo.value.cycle) == {"a", "b"}
        assert len(excinfo.value.cycle) == 2
        assert "a" in str(excinfo.value) and "b" in str(excinfo.value)

    def test_three_node_dependency_cycle_lists_the_loop(self):
        a = _stub("a", deps={"c"})
        b = _stub("b", deps={"a"})
        c = _stub("c", deps={"b"})
        with pytest.raises(ScheduleCycleError) as excinfo:
            schedule([a, b, c], store=None)
        assert set(excinfo.value.cycle) == {"a", "b", "c"}

    def test_duplicate_intent_ids_rejected(self):
        with pytest.raises(ScheduleError, match="duplicate"):
            schedule([_stub("a"), _stub("a")], store=None)

    def test_unknown_dependency_rejected(self):
        with pytest.raises(ScheduleError, match="unknown"):
            schedule([_stub("a", deps={"ghost"})], store=None)


# ---------------------------------------------------------------------------
# Scheduler: hard conflicts
# ---------------------------------------------------------------------------


def _rename_stub(intent_id: str, symbol: str, new: str) -> _StubIntent:
    """An id-changing stub writing ``symbol`` (rename-shaped)."""
    return _stub(
        intent_id,
        fp=Footprint(writes_symbols={symbol}, writes_files={"m.py"}),
        eff=Effect(renamed={symbol: new}, files_written={"m.py"}),
    )


class TestHardConflicts:
    def test_two_renames_of_one_symbol_drop_the_later_one(self):
        r1 = _rename_stub("r1", "m:x", "m:y")
        r2 = _rename_stub("r2", "m:x", "m:z")
        result = schedule([r1, r2], store=None)
        assert _ids(result.ordered) == ["r1"]
        (drop,) = result.dropped
        assert drop.intent.intent_id == "r2"
        assert drop.reason is DropReason.CONFLICT_DROPPED
        assert "m:x" in drop.detail and "r1" in drop.detail

    def test_later_means_submission_order(self):
        r1 = _rename_stub("r1", "m:x", "m:y")
        r2 = _rename_stub("r2", "m:x", "m:z")
        result = schedule([r2, r1], store=None)
        assert _ids(result.ordered) == ["r2"]
        assert result.dropped[0].intent.intent_id == "r1"

    def test_drop_is_deterministic_for_identical_input(self):
        intents = [_rename_stub("r1", "m:x", "m:y"), _rename_stub("r2", "m:x", "m:z")]
        first = schedule(list(intents), store=None)
        second = schedule(list(intents), store=None)
        assert first == second

    def test_dependents_of_dropped_intent_cascade(self):
        r1 = _rename_stub("r1", "m:x", "m:y")
        r2 = _rename_stub("r2", "m:x", "m:z")
        follow = _stub("follow", deps={"r2"})
        result = schedule([r1, r2, follow], store=None)
        assert _ids(result.ordered) == ["r1"]
        assert {d.intent.intent_id for d in result.dropped} == {"r2", "follow"}
        assert all(d.reason is DropReason.CONFLICT_DROPPED for d in result.dropped)

    def test_renames_of_distinct_symbols_are_ordered_not_dropped(self):
        # Prefix overlap (m:Foo vs m:Foo.method) composes via remapping; only
        # the exact same written symbol is a hard conflict.
        r1 = _rename_stub("r1", "m:Foo", "m:Bar")
        r2 = _rename_stub("r2", "m:Foo.method", "m:Foo.run")
        result = schedule([r1, r2], store=None)
        assert sorted(_ids(result.ordered)) == ["r1", "r2"]
        assert result.dropped == ()


# ---------------------------------------------------------------------------
# Simulation: guarded re-validation, orphans, policies
# ---------------------------------------------------------------------------


MOD_XY = "def f():\n    x = 1\n    return x\n"


class TestSimulationGuards:
    def test_inline_of_deleted_variable_drops_with_precondition_reason(
        self, batch_project, tmp_path
    ):
        # A fix deletes the assignment (file-level effect: no orphaning);
        # at the inline's turn its planner re-validates against the overlay
        # and fails to resolve the variable.
        root, store = batch_project({"mod.py": MOD_XY})
        fix = _replace("delete-assignment", "mod.py", "    x = 1\n", "")
        inline = InlineVariableIntent(
            "inline-x", "mod:f:x", deps=frozenset({"delete-assignment"})
        )
        result = run_batch([inline, fix], store, tx_store=TransactionStore(tmp_path / "tx"))
        assert _ids(i.intent for i in result.executed) == ["delete-assignment"]
        (drop,) = result.dropped
        assert drop.intent.intent_id == "inline-x"
        assert drop.reason is DropReason.PRECONDITION_FAILED
        assert "Symbol not found" in drop.detail

    def test_duplicate_inlines_orphan_the_second(self, batch_project, tmp_path):
        # The first inline's effect deletes the anchor; the pending duplicate
        # is remapped through it and dropped as orphaned.
        root, store = batch_project({"mod.py": MOD_XY})
        i1 = InlineVariableIntent("a-inline", "mod:f:x")
        i2 = InlineVariableIntent("b-inline", "mod:f:x")
        result = run_batch([i1, i2], store, tx_store=TransactionStore(tmp_path / "tx"))
        assert _ids(i.intent for i in result.executed) == ["a-inline"]
        (drop,) = result.dropped
        assert drop.intent.intent_id == "b-inline"
        assert drop.reason is DropReason.ORPHANED
        assert "mod:f:x" in drop.detail

    def test_delete_symbol_intent_declines_through_the_real_planner(
        self, batch_project, tmp_path
    ):
        # TASK-124 stage A: delete-symbol now dispatches to a real
        # DeleteSymbolPlanner (refactor/delete.py) instead of the historical
        # "no planner in v1" stub. "mod:f:x" is a VARIABLE, not a
        # FUNCTION/CLASS, so the planner still declines here — but now via
        # its own guarded re-resolution, not a hardcoded refusal.
        root, store = batch_project({"mod.py": MOD_XY})
        delete = DeleteSymbolIntent("del-x", "mod:f:x")
        result = run_batch([delete], store, tx_store=TransactionStore(tmp_path / "tx"))
        assert result.executed == ()
        (drop,) = result.dropped
        assert drop.reason is DropReason.PRECONDITION_FAILED
        assert drop.detail == "symbol 'mod:f:x' is no longer in the index"

    def test_all_or_nothing_aborts_on_execution_drop(self, batch_project, tmp_path):
        root, store = batch_project({"mod.py": MOD_XY})
        fix = _replace("delete-assignment", "mod.py", "    x = 1\n", "")
        inline = InlineVariableIntent(
            "inline-x", "mod:f:x", deps=frozenset({"delete-assignment"})
        )
        with pytest.raises(BatchAborted) as excinfo:
            run_batch(
                [inline, fix],
                store,
                policy=BatchPolicy.ALL_OR_NOTHING,
                tx_store=TransactionStore(tmp_path / "tx"),
            )
        assert excinfo.value.dropped[-1].intent.intent_id == "inline-x"

    def test_dependent_of_runtime_dropped_intent_drops_too(
        self, batch_project, tmp_path
    ):
        root, store = batch_project({"mod.py": MOD_XY})
        bad = InlineVariableIntent("bad-inline", "mod:f:ghost")
        follow = _replace(
            "follow-fix", "mod.py", "return x", "return x",
            deps=frozenset({"bad-inline"}),
        )
        result = run_batch([bad, follow], store, tx_store=TransactionStore(tmp_path / "tx"))
        assert result.executed == ()
        reasons = {d.intent.intent_id: d for d in result.dropped}
        assert reasons["bad-inline"].reason is DropReason.PRECONDITION_FAILED
        assert reasons["follow-fix"].reason is DropReason.PRECONDITION_FAILED
        assert "bad-inline" in reasons["follow-fix"].detail


# ---------------------------------------------------------------------------
# Simulation: interfering renames + anchor remap
# ---------------------------------------------------------------------------


LIB = "def helper():\n    return 1\n"
APP_CALL = "from lib import helper\n\ndef use():\n    x = helper()\n    return x\n"


class TestRenames:
    def test_interfering_renames_skip_and_report(self, batch_project, tmp_path):
        root, store = batch_project({"lib.py": LIB, "app.py": APP_CALL})
        r1 = RenameIntent("r1", "lib:helper", "assist")
        r2 = RenameIntent("r2", "lib:helper", "do_help")
        result = run_batch([r1, r2], store, tx_store=TransactionStore(tmp_path / "tx"))
        assert _ids(i.intent for i in result.executed) == ["r1"]
        (drop,) = result.dropped
        assert (drop.intent.intent_id, drop.reason) == ("r2", DropReason.CONFLICT_DROPPED)
        assert result.store.read_file("lib.py").decode() == "def assist():\n    return 1\n"

    def test_interfering_renames_all_or_nothing_aborts(self, batch_project, tmp_path):
        root, store = batch_project({"lib.py": LIB, "app.py": APP_CALL})
        r1 = RenameIntent("r1", "lib:helper", "assist")
        r2 = RenameIntent("r2", "lib:helper", "do_help")
        before = _snapshot(root)
        tx_store = TransactionStore(tmp_path / "tx")
        with pytest.raises(BatchAborted) as excinfo:
            run_batch(
                [r1, r2],
                store,
                policy=BatchPolicy.ALL_OR_NOTHING,
                tx_store=tx_store,
            )
        assert excinfo.value.dropped[0].intent.intent_id == "r2"
        # Aborted before simulating: no intent was ever re-planned, so nothing
        # was written to the simulation OR persisted as a transaction, and the
        # project (sources and .pypeeker/) is byte-for-byte as it was.
        assert tx_store.list() == []
        assert _snapshot(root) == before

    def test_anchor_remap_through_class_rename(self, batch_project, tmp_path):
        # m:Foo -> m:Bar runs first (tie-break); the pending method rename
        # anchored at mod:Foo.method must follow the substitution and land.
        src = "class Foo:\n    def method(self):\n        return 1\n"
        root, store = batch_project({"mod.py": src})
        r1 = RenameIntent("r1", "mod:Foo", "Bar")
        r2 = RenameIntent("r2", "mod:Foo.method", "run")
        result = run_batch([r1, r2], store, tx_store=TransactionStore(tmp_path / "tx"))
        assert _ids(i.intent for i in result.executed) == ["r1", "r2"]
        assert result.executed[1].intent.symbol_id == "mod:Bar.method"
        assert result.store.read_file("mod.py").decode() == (
            "class Bar:\n    def run(self):\n        return 1\n"
        )
        assert result.dropped == ()
        assert result.effect.remap_id("mod:Foo.method") == "mod:Bar.run"


# ---------------------------------------------------------------------------
# Simulation: chains and end-to-end
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_inline_then_delete_import_chain(self, batch_project, tmp_path):
        # AC3's chain: inline a variable, then a replanning fix deletes the
        # (now unused) import against the post-inline bytes.
        app = "from lib import helper\n\ndef use():\n    x = 1\n    return x\n"
        root, store = batch_project({"lib.py": LIB, "app.py": app})
        inline = InlineVariableIntent("inline-x", "app:use:x")
        drop_import = _replace(
            "drop-import", "app.py", "from lib import helper\n", "",
            deps=frozenset({"inline-x"}),
        )
        result = run_batch([inline, drop_import], store, tx_store=TransactionStore(tmp_path / "tx"))
        assert _ids(i.intent for i in result.executed) == ["inline-x", "drop-import"]
        assert result.dropped == ()
        assert result.store.read_file("app.py").decode() == "\ndef use():\n    return 1\n"
        # The fix's edit was materialized against the post-inline state: its
        # recorded hash matches the bytes the inline left behind, not the
        # original file.
        post_inline = "\ndef use():\n    return 1\n"
        original_hash = hashlib.sha256(app.encode()).hexdigest()
        fix_hash = result.executed[1].edits[0].file_hash
        assert fix_hash != original_hash
        assert fix_hash == hashlib.sha256(
            ("from lib import helper\n" + post_inline).encode()
        ).hexdigest()

    def test_rename_inline_and_fix_across_files(self, batch_project, tmp_path):
        root, store = batch_project(
            {"lib.py": LIB, "app.py": APP_CALL, "other.py": "# TODO: tidy\n"}
        )
        before = _snapshot(root)
        intents = [
            RenameIntent("rename-helper", "lib:helper", "assist"),
            InlineVariableIntent("inline-x", "app:use:x"),
            _replace("fix-todo", "other.py", "TODO", "DONE"),
        ]
        result = run_batch(intents, store, tx_store=TransactionStore(tmp_path / "tx"))

        # Order: the inline (non-id-changing) precedes the conflicting
        # rename; the disjoint fix sorts last by file key.
        assert _ids(i.intent for i in result.executed) == [
            "inline-x",
            "rename-helper",
            "fix-todo",
        ]
        assert result.dropped == ()

        # Hand-computed final state: inline first, then the rename lands on
        # the post-inline call site, then the fix.
        assert result.store.read_file("lib.py").decode() == "def assist():\n    return 1\n"
        assert result.store.read_file("app.py").decode() == (
            "from lib import assist\n\ndef use():\n    return assist()\n"
        )
        assert result.store.read_file("other.py").decode() == "# DONE: tidy\n"

        # Per-intent materialized edits are recorded.
        assert all(intent.edits for intent in result.executed)

        # The folded batch effect maps submitted anchors to final ids.
        assert result.effect.remap_id("lib:helper") == "lib:assist"
        assert result.effect.remap_id("app:use:x") is None

        # The simulated index is fresh for TASK-89's flattening.
        for path in ("lib.py", "app.py", "other.py"):
            assert not result.store.is_stale(path)

        # The REAL project tree is byte-for-byte untouched: the simulation is
        # an in-memory overlay layered directly over the caller's store, with
        # no copy of the project anywhere.
        assert result.store.base is store
        assert _snapshot(root) == before


# ---------------------------------------------------------------------------
# Overlay substrate
#
# Replaces the deleted TestMaterializeMirror one-for-one: the same three
# properties the temp-dir mirror had to establish by copying (indexed files
# visible through the simulation store, simulated content overriding disk,
# deleted files invisible), plus a fourth the mirror could never assert —
# that the real tree, the base store's index, and its in-process cache are
# untouched by the simulation.
# ---------------------------------------------------------------------------


class TestOverlaySubstrate:
    def test_indexed_files_and_indexes_are_visible_through_the_overlay(
        self, batch_project
    ):
        root, store = batch_project({"mod.py": MOD_XY})
        overlay = OverlayIndexStore(store)
        assert overlay.read_file("mod.py") == MOD_XY.encode()
        assert overlay.load("mod.py") is not None
        assert not overlay.is_stale("mod.py")

    def test_simulated_content_overrides_disk(self, batch_project):
        root, store = batch_project({"mod.py": MOD_XY})
        overlay = OverlayIndexStore(store)
        overlay.write_file("mod.py", b"def f():\n    return 2\n")
        rebind(overlay, "mod.py")
        assert overlay.read_file("mod.py") == b"def f():\n    return 2\n"
        assert overlay.file_hash("mod.py") == hashlib.sha256(
            b"def f():\n    return 2\n"
        ).hexdigest()
        assert not overlay.is_stale("mod.py")
        # The real file never saw the simulated bytes.
        assert (root / "mod.py").read_text() == MOD_XY

    def test_deleted_files_are_invisible(self, batch_project):
        root, store = batch_project({"mod.py": MOD_XY, "gone.py": "x = 1\n"})
        overlay = OverlayIndexStore(store)
        overlay.delete_file("gone.py")
        assert not overlay.file_exists("gone.py")
        with pytest.raises(FileNotFoundError):
            overlay.read_file("gone.py")
        assert overlay.file_exists("mod.py")
        assert (root / "gone.py").exists()  # the real file is still there

    def test_simulation_never_reaches_the_base_store_or_its_cache(
        self, batch_project, tmp_path
    ):
        # The property the mirror could not state: a whole simulated batch
        # leaves the base store's on-disk index AND its in-process FileIndex
        # cache exactly as they were.
        root, store = batch_project({"mod.py": MOD_XY})
        baseline = store.load("mod.py")
        assert baseline is not None
        before = _snapshot(root)

        fix = _replace("bump", "mod.py", "x = 1", "x = 2")
        result = run_batch([fix], store, tx_store=TransactionStore(tmp_path / "tx"))
        assert len(result.executed) == 1
        assert result.store.read_file("mod.py").decode() == (
            "def f():\n    x = 2\n    return x\n"
        )

        # The overlay's fresh index is its own; the base store still serves
        # the pre-batch one, from disk and from cache alike.
        assert result.store.load("mod.py") is not baseline
        assert store.load("mod.py") is baseline
        assert IndexStore(root).load("mod.py").file_hash == baseline.file_hash
        assert _snapshot(root) == before


# ---------------------------------------------------------------------------
# Flattening (TASK-89)
# ---------------------------------------------------------------------------


def _mixed_batch(batch_project, tmp_path):
    """A rename + inline + fix batch over a three-file project, simulated.

    Returns ``(root, store, result)``; the batch touches every file and
    composes (the rename lands on the post-inline call site), so it's the
    canonical flattening input.
    """
    root, store = batch_project(
        {"lib.py": LIB, "app.py": APP_CALL, "other.py": "# TODO: tidy\n"}
    )
    intents = [
        RenameIntent("rename-helper", "lib:helper", "assist"),
        InlineVariableIntent("inline-x", "app:use:x"),
        _replace("fix-todo", "other.py", "TODO", "DONE"),
    ]
    result = run_batch(intents, store, tx_store=TransactionStore(tmp_path / "tx"))
    assert result.dropped == ()
    return root, store, result


class TestFlattenBatch:
    def test_one_hash_anchored_entry_per_changed_file(
        self, batch_project, tmp_path
    ):
        root, store, result = _mixed_batch(batch_project, tmp_path)
        header, edits = flatten_batch(result, store)

        assert header.operation == "batch"
        assert (header.symbol_id, header.old_name, header.new_name) == ("", "", "")
        assert sorted(e.file for e in edits) == ["app.py", "lib.py", "other.py"]
        for edit in edits:
            original = (root / edit.file).read_bytes()
            final = result.store.read_file(edit.file)
            # Hash-anchored to the REAL plan-time file, not a simulated state.
            assert edit.file_hash == hashlib.sha256(original).hexdigest()
            # The applier's text guard: old must equal the spanned bytes.
            assert edit.old.encode() == original[edit.start : edit.end]
            # Splicing the entry over the original yields the simulated bytes.
            spliced = (
                original[: edit.start] + edit.new.encode() + original[edit.end :]
            )
            assert spliced == final

    def test_apply_then_rollback_round_trip(self, batch_project, tmp_path):
        from pypeeker.refactor.applier import TransactionApplier
        from pypeeker.storage import TransactionStore

        root, store, result = _mixed_batch(batch_project, tmp_path)
        before = _snapshot(root)
        predicted = {
            path: result.store.read_file(path)
            for path in result.store.overlaid_files()
        }
        header, edits = flatten_batch(result, store)

        tx_store = TransactionStore(root)
        tx_store.save(header, edits)
        applier = TransactionApplier(store, tx_store)
        applied = applier.apply(header.tx_id)
        assert applied["status"] == "applied"
        for path in ("lib.py", "app.py", "other.py"):
            assert (root / path).read_bytes() == predicted[path]

        rolled = applier.rollback(header.tx_id)
        assert rolled["status"] == "rolled_back"
        for path, content in before.items():
            assert (root / path).read_bytes() == content

    def test_entries_trim_common_leading_and_trailing_lines(
        self, batch_project, tmp_path
    ):
        src = "a = 1\nb = 2\nc = 3\n"
        root, store = batch_project({"mod.py": src})
        fix = _replace("bump-b", "mod.py", "b = 2", "b = 20")
        result = run_batch([fix], store, tx_store=TransactionStore(tmp_path / "tx"))
        _, edits = flatten_batch(result, store)

        (edit,) = edits
        assert (edit.start, edit.end) == (len("a = 1\n"), len("a = 1\nb = 2\n"))
        assert (edit.old, edit.new) == ("b = 2\n", "b = 20\n")
        assert edit.old.encode() == src.encode()[edit.start : edit.end]

    def test_net_noop_batch_yields_no_edits(self, batch_project, tmp_path):
        root, store = batch_project({"mod.py": MOD_XY})
        fix = _replace("noop", "mod.py", "return x", "return x")
        result = run_batch([fix], store, tx_store=TransactionStore(tmp_path / "tx"))
        assert len(result.executed) == 1
        header, edits = flatten_batch(result, store)
        assert edits == []
        assert header.operation == "batch"

    def test_created_file_is_an_error(self, batch_project, tmp_path):
        root, store = batch_project({"mod.py": MOD_XY})
        result = run_batch([], store, tx_store=TransactionStore(tmp_path / "tx"))
        result.store.write_file("new.py", b"x = 1\n")
        with pytest.raises(FlattenError, match="created"):
            flatten_batch(result, store)

    def test_deleted_file_is_an_error(self, batch_project, tmp_path):
        root, store = batch_project({"mod.py": MOD_XY})
        result = run_batch([], store, tx_store=TransactionStore(tmp_path / "tx"))
        result.store.delete_file("mod.py")
        with pytest.raises(FlattenError, match="deleted"):
            flatten_batch(result, store)

    def test_executed_file_rename_is_an_error(self, batch_project, tmp_path):
        root, store = batch_project(
            {"helper.py": "def helper():\n    return 1\n"}
        )
        rename = RenameIntent(
            "rename-file", "helper:helper", "assist", include_file=True
        )
        result = run_batch([rename], store, tx_store=TransactionStore(tmp_path / "tx"))
        assert result.executed[0].file_rename is not None
        with pytest.raises(FlattenError, match="renamed"):
            flatten_batch(result, store)


# ---------------------------------------------------------------------------
# The flatten_store seam (PR2): file-set derivation + authorization
# ---------------------------------------------------------------------------


class TestFlattenStoreSeam:
    """``flatten_store`` refuses undeclared file births and deaths.

    The seam takes the authorized sets as explicit parameters rather than
    deriving them from a batch result, so a caller with no ``ExecutedIntent``
    list can still flatten. ``flatten_batch`` passes them empty, which is
    what keeps the v1 created/deleted refusals verbatim.
    """

    def test_unauthorized_created_file_refuses(self, batch_project):
        root, store = batch_project({"mod.py": MOD_XY})
        overlay = OverlayIndexStore(store)
        overlay.write_file("new.py", b"x = 1\n")
        with pytest.raises(FlattenError, match="created") as excinfo:
            flatten_store(overlay, store, operation="batch")
        assert str(excinfo.value) == (
            "file 'new.py' was created in the simulated batch; file "
            "creations cannot be flattened into a single transaction (v1)"
        )

    def test_unauthorized_deleted_file_refuses(self, batch_project):
        root, store = batch_project({"mod.py": MOD_XY})
        overlay = OverlayIndexStore(store)
        overlay.delete_file("mod.py")
        with pytest.raises(FlattenError, match="deleted") as excinfo:
            flatten_store(overlay, store, operation="batch")
        assert str(excinfo.value) == (
            "file 'mod.py' was deleted in the simulated batch; file "
            "deletions cannot be flattened into a single transaction (v1)"
        )

    def test_deletion_is_refused_before_any_creation_is_reported(
        self, batch_project
    ):
        # Ordering is part of the contract the mirror-era code had: the
        # deleted-file wall runs before the per-file loop, so a simulation
        # that both creates and deletes reports the deletion.
        root, store = batch_project({"mod.py": MOD_XY})
        overlay = OverlayIndexStore(store)
        overlay.write_file("new.py", b"x = 1\n")
        overlay.delete_file("mod.py")
        with pytest.raises(FlattenError, match="deleted"):
            flatten_store(overlay, store, operation="batch")

    def test_authorized_sets_suppress_the_refusals(self, batch_project):
        # The seam ITEM B and ITEM D consume: a caller that declares the
        # births and deaths gets a clean flatten. PR2 emits no create/delete
        # entries for them (that is ITEM D's stage) — it only stops refusing.
        root, store = batch_project({"mod.py": MOD_XY})
        overlay = OverlayIndexStore(store)
        overlay.write_file("new.py", b"x = 1\n")
        overlay.delete_file("mod.py")
        header, edits = flatten_store(
            overlay,
            store,
            operation="batch",
            authorized_created=frozenset({"new.py"}),
            authorized_deleted=frozenset({"mod.py"}),
        )
        assert edits == []
        assert header.operation == "batch"

    def test_tombstone_over_a_path_absent_from_the_real_tree_is_not_a_deletion(
        self, batch_project
    ):
        # Nothing is being removed from the user's tree, so there is nothing
        # a flattened transaction would have to express.
        root, store = batch_project({"mod.py": MOD_XY})
        overlay = OverlayIndexStore(store)
        overlay.delete_file("never_existed.py")
        header, edits = flatten_store(overlay, store, operation="batch")
        assert edits == []

    def test_operation_is_the_caller_s_and_untouched_paths_diff_to_nothing(
        self, batch_project
    ):
        root, store = batch_project({"mod.py": MOD_XY, "other.py": "y = 1\n"})
        overlay = OverlayIndexStore(store)
        # Written back byte-identical: recorded as overlaid, diffs to zero.
        overlay.write_file("mod.py", MOD_XY.encode())
        header, edits = flatten_store(overlay, store, operation="privatize")
        assert edits == []
        assert header.operation == "privatize"
        assert (header.symbol_id, header.old_name, header.new_name) == ("", "", "")


# ---------------------------------------------------------------------------
# Simulation isolation (PR2): nothing on disk moves, ever
# ---------------------------------------------------------------------------


def _project_snapshot(root) -> dict[str, bytes]:
    """Every byte under ``root`` — sources AND ``.pypeeker/`` — by relative path.

    :func:`_snapshot`'s walk already covers the storage directory, so this is
    an alias that names the intent: index JSON and transaction JSONL are part
    of the invariant, not just source files.
    """
    return _snapshot(root)


class TestSimulationIsolation:
    """A whole-directory byte snapshot survives every run_batch outcome.

    Under the temp-dir mirror this was true by construction — the simulation
    lived in another directory. Under the overlay ``project_root`` IS the
    real project root, so it has to be proven: for a successful multi-intent
    batch, a mid-batch precondition drop, an orphan drop, and an
    all-or-nothing abort, the sources, ``.pypeeker/index/``, and
    ``.pypeeker/transactions/`` must be identical before and after.
    """

    def _tx_store(self, tmp_path) -> TransactionStore:
        """A scratch transaction store outside the project directory."""
        return TransactionStore(tmp_path / "tx")

    def test_successful_multi_intent_batch_changes_nothing_on_disk(
        self, batch_project, tmp_path
    ):
        root, store = batch_project(
            {"lib.py": LIB, "app.py": APP_CALL, "other.py": "# TODO: tidy\n"}
        )
        before = _project_snapshot(root)
        result = run_batch(
            [
                RenameIntent("rename-helper", "lib:helper", "assist"),
                InlineVariableIntent("inline-x", "app:use:x"),
                _replace("fix-todo", "other.py", "TODO", "DONE"),
            ],
            store,
            tx_store=self._tx_store(tmp_path),
        )
        assert len(result.executed) == 3
        # The simulation really did change things — in memory only.
        assert result.store.overlaid_files() == ["app.py", "lib.py", "other.py"]
        assert _project_snapshot(root) == before
        assert not (root / ".pypeeker" / "transactions").exists()

    def test_precondition_drop_changes_nothing_on_disk(
        self, batch_project, tmp_path
    ):
        root, store = batch_project({"mod.py": MOD_XY})
        before = _project_snapshot(root)
        fix = _replace("delete-assignment", "mod.py", "    x = 1\n", "")
        inline = InlineVariableIntent(
            "inline-x", "mod:f:x", deps=frozenset({"delete-assignment"})
        )
        result = run_batch(
            [inline, fix], store, tx_store=self._tx_store(tmp_path)
        )
        assert result.dropped[0].reason is DropReason.PRECONDITION_FAILED
        assert _project_snapshot(root) == before
        assert not (root / ".pypeeker" / "transactions").exists()

    def test_orphan_drop_changes_nothing_on_disk(self, batch_project, tmp_path):
        root, store = batch_project({"mod.py": MOD_XY})
        before = _project_snapshot(root)
        result = run_batch(
            [
                InlineVariableIntent("a-inline", "mod:f:x"),
                InlineVariableIntent("b-inline", "mod:f:x"),
            ],
            store,
            tx_store=self._tx_store(tmp_path),
        )
        assert result.dropped[0].reason is DropReason.ORPHANED
        assert _project_snapshot(root) == before
        assert not (root / ".pypeeker" / "transactions").exists()

    def test_all_or_nothing_abort_changes_nothing_on_disk(
        self, batch_project, tmp_path
    ):
        root, store = batch_project({"mod.py": MOD_XY})
        before = _project_snapshot(root)
        fix = _replace("delete-assignment", "mod.py", "    x = 1\n", "")
        inline = InlineVariableIntent(
            "inline-x", "mod:f:x", deps=frozenset({"delete-assignment"})
        )
        with pytest.raises(BatchAborted):
            run_batch(
                [inline, fix],
                store,
                policy=BatchPolicy.ALL_OR_NOTHING,
                tx_store=self._tx_store(tmp_path),
            )
        # The abort happens after "delete-assignment" already spliced the
        # simulation — the strong statement is that the disk still does not
        # know about it.
        assert _project_snapshot(root) == before
        assert not (root / ".pypeeker" / "transactions").exists()

    def test_intermediate_transactions_land_only_in_the_scratch_store(
        self, batch_project, tmp_path
    ):
        # The tx_store parameter is the whole reason run_batch cannot derive
        # one from store.project_root: the planners DO persist, and those
        # persisted intermediates must land outside the project.
        root, store = batch_project({"lib.py": LIB, "app.py": APP_CALL})
        tx_store = self._tx_store(tmp_path)
        before = _project_snapshot(root)
        result = run_batch(
            [
                RenameIntent("rename-helper", "lib:helper", "assist"),
                InlineVariableIntent("inline-x", "app:use:x"),
            ],
            store,
            tx_store=tx_store,
        )
        assert len(result.executed) == 2
        assert len(tx_store.list()) == 2  # one per re-planned intent
        assert _project_snapshot(root) == before
        assert TransactionStore(root).list() == []

    def test_base_store_cache_never_observes_a_simulated_index(
        self, batch_project, tmp_path
    ):
        root, store = batch_project({"mod.py": MOD_XY})
        # Warm the base store's in-process cache.
        cached = store.load("mod.py")
        result = run_batch(
            [_replace("bump", "mod.py", "x = 1", "x = 2")],
            store,
            tx_store=self._tx_store(tmp_path),
        )
        simulated = result.store.load("mod.py")
        assert simulated is not None and simulated is not cached
        assert simulated.file_hash != cached.file_hash
        # Same object identity out of the base store: rebind_source's save()
        # landed in the overlay's dict, not the base store's cache.
        assert store.load("mod.py") is cached

    def test_a_nested_simulation_store_still_uses_the_callers_tx_store(
        self, batch_project, tmp_path
    ):
        # run_batch may be handed a store that is itself a simulation store.
        # project_root is STILL the real root under nesting, so the "never
        # derive tx_store from project_root" rule does not weaken.
        root, store = batch_project({"mod.py": MOD_XY})
        outer = OverlayIndexStore(store)
        outer.write_file("mod.py", "def f():\n    x = 5\n    return x\n".encode())
        rebind(outer, "mod.py")
        before = _project_snapshot(root)

        tx_store = self._tx_store(tmp_path)
        result = run_batch(
            [_replace("bump", "mod.py", "x = 5", "x = 6")], store=outer, tx_store=tx_store
        )

        assert result.store.project_root == root
        assert result.store.read_file("mod.py").decode() == (
            "def f():\n    x = 6\n    return x\n"
        )
        assert len(tx_store.list()) == 1
        assert TransactionStore(root).list() == []
        assert _project_snapshot(root) == before


# ---------------------------------------------------------------------------
# Overlay-substrate equivalence (PR2)
# ---------------------------------------------------------------------------


class TestOverlayEquivalence:
    """The canonical mixed batch produces the mirror-era result, field for field.

    ``BatchResult`` lost only ``root``; everything else — the executed
    intents and their order, the per-intent edits, the folded effect, the
    final simulated bytes — and the whole flattened transaction are pinned
    here against the values the temp-dir mirror produced.
    """

    def test_batch_result_matches_the_mirror_era_result(
        self, batch_project, tmp_path
    ):
        root, store, result = _mixed_batch(batch_project, tmp_path)

        assert _ids(i.intent for i in result.executed) == [
            "inline-x",
            "rename-helper",
            "fix-todo",
        ]
        assert result.dropped == ()
        assert result.policy is BatchPolicy.SKIP_AND_REPORT
        assert result.effect.remap_id("lib:helper") == "lib:assist"
        assert result.effect.remap_id("app:use:x") is None

        # The final simulated tree (was: the mirror directory's bytes).
        assert {
            path: result.store.read_file(path).decode()
            for path in result.store.overlaid_files()
        } == {
            "app.py": "from lib import assist\n\ndef use():\n    return assist()\n",
            "lib.py": "def assist():\n    return 1\n",
            "other.py": "# DONE: tidy\n",
        }
        # Every touched file's simulated index is fresh, as the re-saved
        # mirror indexes were.
        for path in ("lib.py", "app.py", "other.py"):
            assert not result.store.is_stale(path)

    def test_flattened_transaction_is_field_for_field_the_mirror_s(
        self, batch_project, tmp_path
    ):
        root, store, result = _mixed_batch(batch_project, tmp_path)
        header, edits = flatten_batch(result, store)

        assert header.operation == "batch"
        assert (header.symbol_id, header.old_name, header.new_name) == ("", "", "")

        originals = {
            path: (root / path).read_bytes()
            for path in ("app.py", "lib.py", "other.py")
        }
        assert [
            (e.file, e.start, e.end, e.old, e.new, e.file_hash, e.op) for e in edits
        ] == [
            (
                "app.py",
                0,
                len(APP_CALL),
                APP_CALL,
                "from lib import assist\n\ndef use():\n    return assist()\n",
                hashlib.sha256(originals["app.py"]).hexdigest(),
                EditOp.REPLACE,
            ),
            (
                "lib.py",
                0,
                len("def helper():\n"),
                "def helper():\n",
                "def assist():\n",
                hashlib.sha256(originals["lib.py"]).hexdigest(),
                EditOp.REPLACE,
            ),
            (
                "other.py",
                0,
                len("# TODO: tidy\n"),
                "# TODO: tidy\n",
                "# DONE: tidy\n",
                hashlib.sha256(originals["other.py"]).hexdigest(),
                EditOp.REPLACE,
            ),
        ]


# ---------------------------------------------------------------------------
# Read-through vocabulary (PR2): the two outcomes the substrate swap moved
# ---------------------------------------------------------------------------


class TestReadThroughVocabulary:
    """The overlay sees the whole real tree; the mirror saw only indexed files.

    Two user-visible outcomes moved with the substrate, both documented in
    architecture.md ("The batch simulation substrate is an in-memory overlay")
    and both pinned here so neither can drift back silently. In both cases the
    new behaviour is what the direct ``app.submit.submit_intent`` path always
    did against the real store — the batch engine was the outlier.
    """

    def test_unindexed_file_on_disk_is_edited_instead_of_dropped(
        self, batch_project, tmp_path
    ):
        # Was: materialize_mirror never copied a file with no index entry, so
        # the planner's FileExists precondition declined and the intent
        # dropped PRECONDITION_FAILED. Now: the overlay reads through to disk,
        # the intent executes, and the flattened transaction edits a file the
        # index has never heard of.
        root, store = batch_project({"mod.py": MOD_XY})
        (root / "notindexed.py").write_text("ZZZ = 1\n")
        assert store.list_indexed_files() == ["mod.py"]
        before = _project_snapshot(root)

        result = run_batch(
            [_replace("touch-nonindexed", "notindexed.py", "ZZZ", "YYY")],
            store,
            tx_store=TransactionStore(tmp_path / "tx"),
        )

        assert _ids(i.intent for i in result.executed) == ["touch-nonindexed"]
        assert result.dropped == ()
        assert result.store.read_file("notindexed.py") == b"YYY = 1\n"

        header, edits = flatten_batch(result, store)
        original = (root / "notindexed.py").read_bytes()
        assert [
            (e.file, e.start, e.end, e.old, e.new, e.file_hash) for e in edits
        ] == [
            (
                "notindexed.py",
                0,
                len(original),
                "ZZZ = 1\n",
                "YYY = 1\n",
                hashlib.sha256(original).hexdigest(),
            )
        ]
        # Still only a plan: the edit is hash-anchored to the real plan-time
        # bytes and nothing on disk moved.
        assert store.load("notindexed.py") is None
        assert _project_snapshot(root) == before

    def test_orphan_index_entry_makes_the_freshness_guard_drop_the_intent(
        self, batch_project, tmp_path
    ):
        # Was: materialize_mirror skipped a file it could not read AND its
        # index entry, so the orphan was invisible to the simulation and the
        # rename executed against the surviving files. Now: the overlay's
        # list_indexed_files reads through to the base, the orphan's index
        # entry still contributes a reference, and AffectedFilesFresh refuses.
        root, store = batch_project(
            {
                "pkg/__init__.py": "",
                "pkg/core.py": "def helper():\n    return 1\n",
                "pkg/other.py": "from pkg.core import helper\n\nhelper()\n",
            }
        )
        (root / "pkg" / "other.py").unlink()
        assert "pkg/other.py" in store.list_indexed_files()  # the orphan entry
        before = _project_snapshot(root)

        result = run_batch(
            [RenameIntent("r", "pkg.core:helper", "assist")],
            store,
            tx_store=TransactionStore(tmp_path / "tx"),
        )

        assert result.executed == ()
        (drop,) = result.dropped
        assert (drop.intent.intent_id, drop.reason) == (
            "r",
            DropReason.PRECONDITION_FAILED,
        )
        assert drop.precondition == "affected-files-fresh"
        assert drop.detail == (
            "File 'pkg/other.py' is stale or not indexed. Run 'pypeeker index' first."
        )
        assert _project_snapshot(root) == before

    def test_pruning_the_orphan_entry_makes_the_rename_execute_again(
        self, batch_project, tmp_path
    ):
        # The escape hatch the CLI takes for you: `main`'s ensure_fresh drops
        # index entries whose source file is gone, so the drop above is only
        # reachable under --no-refresh (or from a library caller).
        root, store = batch_project(
            {
                "pkg/__init__.py": "",
                "pkg/core.py": "def helper():\n    return 1\n",
                "pkg/other.py": "from pkg.core import helper\n\nhelper()\n",
            }
        )
        (root / "pkg" / "other.py").unlink()
        ensure_fresh(store, root)
        assert "pkg/other.py" not in store.list_indexed_files()

        result = run_batch(
            [RenameIntent("r", "pkg.core:helper", "assist")],
            store,
            tx_store=TransactionStore(tmp_path / "tx"),
        )

        assert _ids(i.intent for i in result.executed) == ["r"]
        assert result.dropped == ()
        assert result.store.read_file("pkg/core.py").decode() == (
            "def assist():\n    return 1\n"
        )
