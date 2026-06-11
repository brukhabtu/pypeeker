"""Tests for the batch scheduler + mirror simulation loop (TASK-88).

Scheduler tests use stub intents with canned footprints/effects (the
scheduler is pure over the intent protocol); simulation tests run real
planner-backed intents against a temp mirror of an indexed project and
assert the mirror's final bytes while the real project stays untouched.
"""

from __future__ import annotations

import dataclasses
import hashlib
from typing import ClassVar

import pytest

from pypeeker.binder.binder import bind
from pypeeker.models.transaction import EditEntry, EditOp
from pypeeker.refactor.batch import (
    BatchAborted,
    BatchPolicy,
    DropReason,
    FlattenError,
    ScheduleCycleError,
    ScheduleError,
    flatten_batch,
    materialize_mirror,
    run_batch,
    schedule,
)
from pypeeker.refactor.footprint import EMPTY_EFFECT, EMPTY_FOOTPRINT, Effect, Footprint
from pypeeker.refactor.intents import (
    DeleteSymbolIntent,
    FixIntent,
    InlineVariableIntent,
    Intent,
    RenameIntent,
)
from pypeeker.refactor.simulate import rebind
from pypeeker.storage import IndexStore, OverlayIndexStore


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


@dataclasses.dataclass(frozen=True)
class _Declined:
    """Decline-shaped fix result (no ``edits`` attribute)."""

    reason: str


@dataclasses.dataclass(frozen=True)
class _Planned:
    """Plan-shaped fix result carrying ``edits``."""

    edits: tuple[EditEntry, ...]


@dataclasses.dataclass(frozen=True)
class _ReplaceOnceFix:
    """Replannable stub fix: replace the first ``target`` in ``path``.

    Re-anchors against the *current* bytes of the store it is planned over
    (the real project at schedule time, the mirror at execution time) and
    declines when the target text is gone — the Fix-contract behaviour
    FixIntent relies on.
    """

    path: str
    target: str
    replacement: str
    fix_id: str = "stub:replace-once"
    description: str = "replace a byte sequence once"

    def plan(self, store) -> object:
        """Plan a single replace edit against current bytes, or decline."""
        content = (store.project_root / self.path).read_bytes()
        needle = self.target.encode("utf-8")
        at = content.find(needle)
        if at < 0:
            return _Declined(f"{self.target!r} not found in {self.path}")
        op = EditOp.DELETE if not self.replacement else EditOp.REPLACE
        return _Planned(
            (
                EditEntry(
                    op=op,
                    file=self.path,
                    start=at,
                    end=at + len(needle),
                    old=self.target,
                    new=self.replacement,
                    file_hash=hashlib.sha256(content).hexdigest(),
                ),
            )
        )


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
        # at the inline's turn its planner re-validates against the mirror
        # and fails to resolve the variable.
        root, store = batch_project({"mod.py": MOD_XY})
        fix = FixIntent(
            "delete-assignment", _ReplaceOnceFix("mod.py", "    x = 1\n", "")
        )
        inline = InlineVariableIntent(
            "inline-x", "mod:f:x", deps=frozenset({"delete-assignment"})
        )
        result = run_batch([inline, fix], store, work_dir=tmp_path / "mirror")
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
        result = run_batch([i1, i2], store, work_dir=tmp_path / "mirror")
        assert _ids(i.intent for i in result.executed) == ["a-inline"]
        (drop,) = result.dropped
        assert drop.intent.intent_id == "b-inline"
        assert drop.reason is DropReason.ORPHANED
        assert "mod:f:x" in drop.detail

    def test_delete_symbol_intent_is_schedulable_but_not_executable(
        self, batch_project, tmp_path
    ):
        root, store = batch_project({"mod.py": MOD_XY})
        delete = DeleteSymbolIntent("del-x", "mod:f:x")
        result = run_batch([delete], store, work_dir=tmp_path / "mirror")
        assert result.executed == ()
        (drop,) = result.dropped
        assert drop.reason is DropReason.PRECONDITION_FAILED
        assert "no planner" in drop.detail

    def test_all_or_nothing_aborts_on_execution_drop(self, batch_project, tmp_path):
        root, store = batch_project({"mod.py": MOD_XY})
        fix = FixIntent(
            "delete-assignment", _ReplaceOnceFix("mod.py", "    x = 1\n", "")
        )
        inline = InlineVariableIntent(
            "inline-x", "mod:f:x", deps=frozenset({"delete-assignment"})
        )
        with pytest.raises(BatchAborted) as excinfo:
            run_batch(
                [inline, fix],
                store,
                policy=BatchPolicy.ALL_OR_NOTHING,
                work_dir=tmp_path / "mirror",
            )
        assert excinfo.value.dropped[-1].intent.intent_id == "inline-x"

    def test_dependent_of_runtime_dropped_intent_drops_too(
        self, batch_project, tmp_path
    ):
        root, store = batch_project({"mod.py": MOD_XY})
        bad = InlineVariableIntent("bad-inline", "mod:f:ghost")
        follow = FixIntent(
            "follow-fix",
            _ReplaceOnceFix("mod.py", "return x", "return x"),
            deps=frozenset({"bad-inline"}),
        )
        result = run_batch([bad, follow], store, work_dir=tmp_path / "mirror")
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
        result = run_batch([r1, r2], store, work_dir=tmp_path / "mirror")
        assert _ids(i.intent for i in result.executed) == ["r1"]
        (drop,) = result.dropped
        assert (drop.intent.intent_id, drop.reason) == ("r2", DropReason.CONFLICT_DROPPED)
        assert (result.root / "lib.py").read_text() == "def assist():\n    return 1\n"

    def test_interfering_renames_all_or_nothing_aborts(self, batch_project, tmp_path):
        root, store = batch_project({"lib.py": LIB, "app.py": APP_CALL})
        r1 = RenameIntent("r1", "lib:helper", "assist")
        r2 = RenameIntent("r2", "lib:helper", "do_help")
        with pytest.raises(BatchAborted) as excinfo:
            run_batch(
                [r1, r2],
                store,
                policy=BatchPolicy.ALL_OR_NOTHING,
                work_dir=tmp_path / "mirror",
            )
        assert excinfo.value.dropped[0].intent.intent_id == "r2"
        assert not (tmp_path / "mirror").exists()  # aborted before simulating

    def test_anchor_remap_through_class_rename(self, batch_project, tmp_path):
        # m:Foo -> m:Bar runs first (tie-break); the pending method rename
        # anchored at mod:Foo.method must follow the substitution and land.
        src = "class Foo:\n    def method(self):\n        return 1\n"
        root, store = batch_project({"mod.py": src})
        r1 = RenameIntent("r1", "mod:Foo", "Bar")
        r2 = RenameIntent("r2", "mod:Foo.method", "run")
        result = run_batch([r1, r2], store, work_dir=tmp_path / "mirror")
        assert _ids(i.intent for i in result.executed) == ["r1", "r2"]
        assert result.executed[1].intent.symbol_id == "mod:Bar.method"
        assert (result.root / "mod.py").read_text() == (
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
        drop_import = FixIntent(
            "drop-import",
            _ReplaceOnceFix("app.py", "from lib import helper\n", ""),
            deps=frozenset({"inline-x"}),
        )
        result = run_batch([inline, drop_import], store, work_dir=tmp_path / "mirror")
        assert _ids(i.intent for i in result.executed) == ["inline-x", "drop-import"]
        assert result.dropped == ()
        assert (result.root / "app.py").read_text() == "\ndef use():\n    return 1\n"
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
            FixIntent("fix-todo", _ReplaceOnceFix("other.py", "TODO", "DONE")),
        ]
        result = run_batch(intents, store, work_dir=tmp_path / "mirror")

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
        assert (result.root / "lib.py").read_text() == "def assist():\n    return 1\n"
        assert (result.root / "app.py").read_text() == (
            "from lib import assist\n\ndef use():\n    return assist()\n"
        )
        assert (result.root / "other.py").read_text() == "# DONE: tidy\n"

        # Per-intent materialized edits are recorded.
        assert all(intent.edits for intent in result.executed)

        # The folded batch effect maps submitted anchors to final ids.
        assert result.effect.remap_id("lib:helper") == "lib:assist"
        assert result.effect.remap_id("app:use:x") is None

        # The mirror index is fresh for TASK-89's flattening.
        for path in ("lib.py", "app.py", "other.py"):
            assert not result.store.is_stale(path)

        # The REAL project tree is byte-for-byte untouched.
        assert result.root != root
        assert _snapshot(root) == before


# ---------------------------------------------------------------------------
# Mirror substrate
# ---------------------------------------------------------------------------


class TestMaterializeMirror:
    def test_mirror_copies_indexed_files_and_indexes(self, batch_project, tmp_path):
        root, store = batch_project({"mod.py": MOD_XY})
        mirror = materialize_mirror(store, tmp_path / "mirror")
        assert (tmp_path / "mirror" / "mod.py").read_bytes() == MOD_XY.encode()
        assert mirror.load("mod.py") is not None
        assert not mirror.is_stale("mod.py")

    def test_mirror_reads_through_an_overlay(self, batch_project, tmp_path):
        # Overlay-simulated content feeds the mirror: the v1 substrate keeps
        # OverlayIndexStore in the loop as an input layer.
        root, store = batch_project({"mod.py": MOD_XY})
        overlay = OverlayIndexStore(store)
        overlay.write_file("mod.py", b"def f():\n    return 2\n")
        rebind(overlay, "mod.py")
        mirror = materialize_mirror(overlay, tmp_path / "mirror")
        assert (tmp_path / "mirror" / "mod.py").read_bytes() == (
            b"def f():\n    return 2\n"
        )
        assert not mirror.is_stale("mod.py")
        # The real file and base store never saw the overlay bytes.
        assert (root / "mod.py").read_text() == MOD_XY

    def test_overlay_deleted_files_are_skipped(self, batch_project, tmp_path):
        root, store = batch_project({"mod.py": MOD_XY, "gone.py": "x = 1\n"})
        overlay = OverlayIndexStore(store)
        overlay.delete_file("gone.py")
        materialize_mirror(overlay, tmp_path / "mirror")
        assert not (tmp_path / "mirror" / "gone.py").exists()
        assert (tmp_path / "mirror" / "mod.py").exists()


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
        FixIntent("fix-todo", _ReplaceOnceFix("other.py", "TODO", "DONE")),
    ]
    result = run_batch(intents, store, work_dir=tmp_path / "mirror")
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
            final = (result.root / edit.file).read_bytes()
            # Hash-anchored to the REAL plan-time file, not a mirror state.
            assert edit.file_hash == hashlib.sha256(original).hexdigest()
            # The applier's text guard: old must equal the spanned bytes.
            assert edit.old.encode() == original[edit.start : edit.end]
            # Splicing the entry over the original yields the mirror's bytes.
            spliced = (
                original[: edit.start] + edit.new.encode() + original[edit.end :]
            )
            assert spliced == final

    def test_apply_then_rollback_round_trip(self, batch_project, tmp_path):
        from pypeeker.refactor.applier import TransactionApplier
        from pypeeker.storage import TransactionStore

        root, store, result = _mixed_batch(batch_project, tmp_path)
        before = _snapshot(root)
        predicted = _snapshot(result.root)
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
        fix = FixIntent("bump-b", _ReplaceOnceFix("mod.py", "b = 2", "b = 20"))
        result = run_batch([fix], store, work_dir=tmp_path / "mirror")
        _, edits = flatten_batch(result, store)

        (edit,) = edits
        assert (edit.start, edit.end) == (len("a = 1\n"), len("a = 1\nb = 2\n"))
        assert (edit.old, edit.new) == ("b = 2\n", "b = 20\n")
        assert edit.old.encode() == src.encode()[edit.start : edit.end]

    def test_net_noop_batch_yields_no_edits(self, batch_project, tmp_path):
        root, store = batch_project({"mod.py": MOD_XY})
        fix = FixIntent("noop", _ReplaceOnceFix("mod.py", "return x", "return x"))
        result = run_batch([fix], store, work_dir=tmp_path / "mirror")
        assert len(result.executed) == 1
        header, edits = flatten_batch(result, store)
        assert edits == []
        assert header.operation == "batch"

    def test_created_file_is_an_error(self, batch_project, tmp_path):
        root, store = batch_project({"mod.py": MOD_XY})
        result = run_batch([], store, work_dir=tmp_path / "mirror")
        (result.root / "new.py").write_text("x = 1\n")
        with pytest.raises(FlattenError, match="created"):
            flatten_batch(result, store)

    def test_deleted_file_is_an_error(self, batch_project, tmp_path):
        root, store = batch_project({"mod.py": MOD_XY})
        result = run_batch([], store, work_dir=tmp_path / "mirror")
        (result.root / "mod.py").unlink()
        with pytest.raises(FlattenError, match="deleted"):
            flatten_batch(result, store)

    def test_executed_file_rename_is_an_error(self, batch_project, tmp_path):
        root, store = batch_project(
            {"helper.py": "def helper():\n    return 1\n"}
        )
        rename = RenameIntent(
            "rename-file", "helper:helper", "assist", include_file=True
        )
        result = run_batch([rename], store, work_dir=tmp_path / "mirror")
        assert result.executed[0].file_rename is not None
        with pytest.raises(FlattenError, match="renamed"):
            flatten_batch(result, store)
