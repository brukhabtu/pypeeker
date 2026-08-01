"""File birth and death through the batch engine (TASK-131, Plan D PR2).

Three layers, in the order the engine sees them:

* the :class:`~pypeeker.intents.footprint.Effect` algebra's file half — one
  test per composition law in the plan (create -> delete, delete -> create,
  create -> write, create -> rename);
* the simulation loop and the scheduler — hand-built intents whose
  materializers declare births/deaths, proving the overlay mutations, the
  determinism rules, and the machine-readable drops;
* :func:`~pypeeker.refactor.batch.flatten_store`'s authorized emission,
  round-tripped through the *real* applier so the entries are proven by
  apply/rollback rather than by inspection.

No builtin planner declares a birth or a death yet (that is PR3's
move-symbol), so the intents here are test-registered — the
``ReplaceTextIntent`` precedent in ``test_batch.py``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import itertools
from pathlib import Path
from typing import ClassVar

import pytest

from pypeeker.binder.binder import bind
from pypeeker.intents import (
    EMPTY_EFFECT,
    Effect,
    Footprint,
    Intent,
    RenameIntent,
    ReplaceTextIntent,
)
from pypeeker.models import EditEntry, EditOp
from pypeeker.refactor import registry
from pypeeker.refactor.applier import TransactionApplier
from pypeeker.refactor.batch import (
    DropReason,
    FlattenError,
    flatten_batch,
    flatten_store,
    run_batch,
    schedule,
)
from pypeeker.refactor.registry import Materialized, register_planner
from pypeeker.storage import IndexStore, OverlayIndexStore, TransactionStore

# ---------------------------------------------------------------------------
# Effect algebra: the file-lifecycle composition table
# ---------------------------------------------------------------------------

_LIFECYCLE_ATOMS = {
    "create p": Effect(files_created={"p.py"}, files_written={"p.py"}),
    "create q": Effect(files_created={"q.py"}, files_written={"q.py"}),
    "delete p": Effect(files_deleted={"p.py"}),
    "delete q": Effect(files_deleted={"q.py"}),
    "write p": Effect(files_written={"p.py"}),
    "write q": Effect(files_written={"q.py"}),
    "rename p->q": Effect(files_renamed={"p.py": "q.py"}),
    "rename q->p": Effect(files_renamed={"q.py": "p.py"}),
}
"""The single-intent effect shapes a file-lifecycle planner can declare.

Two paths are enough: every composition law is about one path's lifecycle
and at most one rename edge between two of them.
"""


def _replay_atoms(initial: frozenset[str], names) -> frozenset[str] | None:
    """The path set after applying each named atom in turn, or None if unreal.

    ``None`` means a real tree could not perform the sequence at all
    (creating over an occupied path, deleting or renaming what is not there),
    so there is no ground truth for the composite to agree with.
    """
    state = set(initial)
    for name in names:
        effect = _LIFECYCLE_ATOMS[name]
        for path in sorted(effect.files_created):
            if path in state:
                return None
            state.add(path)
        if not effect.files_written <= state:
            return None
        for path in sorted(effect.files_deleted):
            if path not in state:
                return None
            state.discard(path)
        for old, new in effect.files_renamed:
            if old not in state or new in state:
                return None
            state.discard(old)
            state.add(new)
    return frozenset(state)


def _replay_composite(initial: frozenset[str], effect: Effect) -> frozenset[str] | None:
    """The path set after applying one *composite* effect, or None if unreal.

    A composite is applied deaths-first, then renames, then births — the only
    order that works for every composite the algebra emits (``delete p`` +
    ``rename q->p`` needs the death first; ``rename p->q`` + ``create p``
    needs the rename first). An identity rename entry (``p -> p``) is a
    harmless no-op the algebra may carry out of a cancelling rename pair, so
    it is skipped rather than treated as a collision.
    """
    state = set(initial)
    for path in sorted(effect.files_deleted):
        if path not in state:
            return None
        state.discard(path)
    for old, new in effect.files_renamed:
        if old == new:
            continue
        if old not in state or new in state:
            return None
        state.discard(old)
        state.add(new)
    for path in sorted(effect.files_created):
        if path in state:
            return None
        state.add(path)
    return frozenset(state)


class TestEffectFileLifecycleComposition:
    """One assertion per composition law the plan names."""

    def test_create_then_delete_cancels(self):
        e1 = Effect(files_created={"new.py"}, files_written={"new.py"})
        e2 = Effect(files_deleted={"new.py"})
        composed = e1.then(e2)
        assert composed.files_created == frozenset()
        assert composed.files_deleted == frozenset()
        # The write cancels too: a file that never existed has no bytes that
        # could have changed.
        assert composed.files_written == frozenset()

    def test_delete_then_create_nets_to_a_write(self):
        e1 = Effect(files_deleted={"mod.py"})
        e2 = Effect(files_created={"mod.py"})
        composed = e1.then(e2)
        assert composed.files_created == frozenset()
        assert composed.files_deleted == frozenset()
        assert composed.files_written == frozenset({"mod.py"})

    def test_create_then_write_stays_a_create(self):
        e1 = Effect(files_created={"new.py"})
        e2 = Effect(files_written={"new.py"})
        composed = e1.then(e2)
        assert composed.files_created == frozenset({"new.py"})
        assert composed.files_written == frozenset({"new.py"})
        assert composed.files_deleted == frozenset()

    def test_create_then_rename_carries_the_creation(self):
        e1 = Effect(files_created={"new.py"}, files_written={"new.py"})
        e2 = Effect(files_renamed={"new.py": "moved.py"})
        composed = e1.then(e2)
        assert composed.files_created == frozenset({"moved.py"})
        assert composed.files_written == frozenset({"moved.py"})
        # No rename entry: a file born inside the composition has no
        # pre-image path the composite could name as the rename source.
        assert composed.files_renamed == ()
        # ...because the newborn has no pre-image at all under e1.
        assert e1._preimage_file("new.py") is None
        assert e1._preimage_file("untouched.py") == "untouched.py"

    def test_rename_then_delete_deletes_the_original(self):
        # The file analogue of "a rename whose target other deletes becomes a
        # deletion of the original id".
        e1 = Effect(files_renamed={"old.py": "new.py"})
        e2 = Effect(files_deleted={"new.py"})
        composed = e1.then(e2)
        assert composed.files_deleted == frozenset({"old.py"})
        assert composed.files_renamed == ()

    def test_write_then_delete_drops_the_write(self):
        e1 = Effect(files_written={"mod.py"})
        e2 = Effect(files_deleted={"mod.py"})
        composed = e1.then(e2)
        assert composed.files_written == frozenset()
        assert composed.files_deleted == frozenset({"mod.py"})

    def test_unrelated_lifecycle_entries_both_survive(self):
        e1 = Effect(files_created={"a.py"}, files_deleted={"b.py"})
        e2 = Effect(files_created={"c.py"}, files_deleted={"d.py"})
        composed = e1.then(e2)
        assert composed.files_created == frozenset({"a.py", "c.py"})
        assert composed.files_deleted == frozenset({"b.py", "d.py"})

    def test_empty_effect_is_identity_over_the_file_sets(self):
        effect = Effect(
            files_written={"m.py"},
            files_created={"born.py"},
            files_deleted={"dead.py"},
        )
        assert EMPTY_EFFECT.then(effect) == effect
        assert effect.then(EMPTY_EFFECT) == effect

    @pytest.mark.parametrize(
        "witness",
        [
            ("delete p", "create q", "rename q->p"),
            ("create q", "delete p", "rename q->p"),
            ("rename p->q", "create p", "delete q"),
        ],
    )
    def test_a_creation_carried_by_a_rename_cancels_a_deletion(self, witness):
        """delete -> create cancels even when the rename is what lands them together.

        :func:`~pypeeker.refactor.batch.run_batch` folds **left**
        (``total_effect = total_effect.then(effect)``), so the cancellation
        has to be computed on the composite's own sets. In each witness the
        creation only arrives at ``p.py`` through the third effect's rename,
        which no comparison of the two operands' *declared* sets can see:
        the composite claimed ``p.py`` as created **and** deleted at once —
        a state no tree can be in, and (via
        :func:`~pypeeker.refactor.batch.flatten_batch`, which hands
        :attr:`BatchResult.effect` straight to ``flatten_store`` as
        ``authorized_created``/``authorized_deleted``) an authorization to
        both bear and bury one file. ``p.py`` existed throughout and had its
        bytes replaced, so the honest composite is a plain write.
        """
        first, second, third = (_LIFECYCLE_ATOMS[name] for name in witness)

        left = first.then(second).then(third)
        assert left.files_created == frozenset()
        assert left.files_deleted == frozenset()
        assert left.files_written == frozenset({"p.py"})
        assert left.files_renamed == ()
        # Right-association always gave this answer; matching on the composite
        # sets is what makes the law associative over these triples again.
        assert left == first.then(second.then(third))

    @pytest.mark.parametrize("length", [1, 2, 3])
    def test_composition_agrees_with_a_reference_model(self, length):
        """Every realizable atom sequence composes to a composite that replays it.

        The exhaustive counterpart of the hand-written laws above: over every
        sequence of file-lifecycle atoms that a real tree can actually
        perform, the left-folded composite must (a) be applicable to the
        starting tree and land on exactly the final path set, (b) never claim
        one path as both created and deleted, and (c) never name a path that
        is gone as written. This is the shape of check that caught the
        rename-carried creation above — the divergences were invisible to
        every per-law assertion because they need three effects to appear.
        """
        checked = 0
        for initial in (
            frozenset(),
            frozenset({"p.py"}),
            frozenset({"q.py"}),
            frozenset({"p.py", "q.py"}),
        ):
            for names in itertools.product(_LIFECYCLE_ATOMS, repeat=length):
                final = _replay_atoms(initial, names)
                if final is None:
                    continue  # the tree cannot perform this sequence
                checked += 1
                composite = EMPTY_EFFECT
                for name in names:
                    composite = composite.then(_LIFECYCLE_ATOMS[name])
                where = f"{sorted(initial)} then {names}"
                assert _replay_composite(initial, composite) == final, where
                assert not (
                    composite.files_created & composite.files_deleted
                ), where
                assert composite.files_written <= final, where
        assert checked  # the corpus is not vacuously empty


# ---------------------------------------------------------------------------
# Test-registered intents that declare births and deaths
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _CreateFileIntent(Intent):
    """Bring ``path`` into existence with ``content``, declaring the birth."""

    path: str
    content: str

    kind: ClassVar[str] = "test-only:create-file"

    def footprint(self, store) -> Footprint:
        """Writes the newborn path (unconditionally — it is not existence-gated)."""
        return Footprint(writes_files={self.path})

    def predicted_effect(self, store) -> Effect:
        """Declares the birth, which is what authorizes the flattened entry."""
        return Effect(files_created={self.path}, files_written={self.path})

    def remap(self, effect) -> Intent:
        """Identity: a path anchor follows no substitution here."""
        return self


@dataclasses.dataclass(frozen=True)
class _UndeclaredCreateIntent(_CreateFileIntent):
    """A birth the materializer performs but the effect never claims."""

    kind: ClassVar[str] = "test-only:undeclared-create"

    def predicted_effect(self, store) -> Effect:
        """Under-declared on purpose: a write, but no birth."""
        return Effect(files_written={self.path})


@dataclasses.dataclass(frozen=True)
class _DeleteFileIntent(Intent):
    """Take ``path`` out of existence, declaring the death."""

    path: str

    kind: ClassVar[str] = "test-only:delete-file"

    def footprint(self, store) -> Footprint:
        """Reads and writes the doomed path."""
        return Footprint(reads_files={self.path}, writes_files={self.path})

    def predicted_effect(self, store) -> Effect:
        """Declares the death."""
        return Effect(files_deleted={self.path})

    def remap(self, effect) -> Intent:
        """Identity."""
        return self


@dataclasses.dataclass(frozen=True)
class _CreateAndEditIntent(Intent):
    """Bring ``path`` into existence *and* edit ``edits_path``.

    The two-sided shape a move-symbol intent has: one file is born, another
    (which may be a different intent's newborn) is rewritten. Both are
    declared in the footprint and in the predicted effect.
    """

    path: str
    content: str
    edits_path: str

    kind: ClassVar[str] = "test-only:create-and-edit"

    def footprint(self, store) -> Footprint:
        """Reads and writes both paths, unconditionally."""
        both = {self.path, self.edits_path}
        return Footprint(reads_files=both, writes_files=both)

    def predicted_effect(self, store) -> Effect:
        """Declares the birth plus the write of the edited path."""
        return Effect(
            files_created={self.path},
            files_written={self.path, self.edits_path},
        )

    def remap(self, effect) -> Intent:
        """Identity."""
        return self


@dataclasses.dataclass(frozen=True)
class _EditMissingFileIntent(Intent):
    """Materializes a byte edit against a path the simulation does not have."""

    path: str

    kind: ClassVar[str] = "test-only:edit-missing"

    def footprint(self, store) -> Footprint:
        """Reads and writes the (absent) path."""
        return Footprint(reads_files={self.path}, writes_files={self.path})

    def predicted_effect(self, store) -> Effect:
        """A plain write — the intent believes the file is there."""
        return Effect(files_written={self.path})

    def remap(self, effect) -> Intent:
        """Identity."""
        return self


@pytest.fixture(autouse=True)
def lifecycle_kinds():
    """Register the test-only materializers; unregister them afterwards."""

    @register_planner(_CreateFileIntent.kind)
    def _materialize_create(intent, store, tx_store):
        return Materialized(files_created={intent.path: intent.content.encode()})

    @register_planner(_UndeclaredCreateIntent.kind)
    def _materialize_undeclared(intent, store, tx_store):
        return Materialized(files_created={intent.path: intent.content.encode()})

    @register_planner(_DeleteFileIntent.kind)
    def _materialize_delete(intent, store, tx_store):
        return Materialized(files_deleted=[intent.path])

    @register_planner(_CreateAndEditIntent.kind)
    def _materialize_create_and_edit(intent, store, tx_store):
        return Materialized(
            files_created={intent.path: intent.content.encode()},
            edits=[
                EditEntry(
                    file=intent.edits_path,
                    start=0,
                    end=0,
                    old="",
                    new=f"# touched by {intent.intent_id}\n",
                    file_hash="",
                    op=EditOp.INSERT,
                )
            ],
        )

    @register_planner(_EditMissingFileIntent.kind)
    def _materialize_edit_missing(intent, store, tx_store):
        return Materialized(
            edits=[
                EditEntry(
                    file=intent.path,
                    start=0,
                    end=0,
                    old="",
                    new="# added\n",
                    file_hash="",
                    op=EditOp.INSERT,
                )
            ]
        )

    try:
        yield
    finally:
        for kind in (
            _CreateFileIntent.kind,
            _UndeclaredCreateIntent.kind,
            _DeleteFileIntent.kind,
            _CreateAndEditIntent.kind,
            _EditMissingFileIntent.kind,
        ):
            registry._REGISTRY.pop(kind, None)


MOD = "def f():\n    x = 1\n    return x\n"


@pytest.fixture
def project(tmp_path, adapter):
    """``files -> (root, store)``: an indexed project under ``tmp_path/proj``."""

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


def _tx(tmp_path: Path) -> TransactionStore:
    """A scratch transaction store for the simulated re-plans."""
    return TransactionStore(tmp_path / "tx")


def _snapshot(root: Path) -> dict[str, bytes]:
    """Every file under ``root``, by relative path."""
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _sources(snapshot: dict[str, bytes]) -> dict[str, bytes]:
    """A snapshot's source files, i.e. everything outside ``.pypeeker/``."""
    return {
        path: content
        for path, content in snapshot.items()
        if not path.startswith(".pypeeker")
    }


# ---------------------------------------------------------------------------
# The simulation loop: birth, death, and the drops around them
# ---------------------------------------------------------------------------


class TestEngineBirthAndDeath:
    def test_a_file_born_mid_batch_is_editable_by_a_later_intent(
        self, project, tmp_path
    ):
        root, store = project({"mod.py": MOD})
        create = _CreateFileIntent("born", path="new.py", content="x = 1\n")
        edit = ReplaceTextIntent("bump", "new.py", 0, 0, "x = 1", "x = 2")

        result = run_batch([create, edit], store, tx_store=_tx(tmp_path))

        assert result.dropped == ()
        assert [e.intent.intent_id for e in result.executed] == ["born", "bump"]
        assert result.store.read_file("new.py") == b"x = 2\n"
        # The overlay's index layer knows the newborn too (it was re-bound).
        assert result.store.load("new.py") is not None
        assert result.effect.files_created == frozenset({"new.py"})
        # Nothing reached the real tree.
        assert not (root / "new.py").exists()

    def test_birth_before_edit_is_independent_of_submission_order(
        self, project, tmp_path
    ):
        root, store = project({"mod.py": MOD})
        create = _CreateFileIntent("born", path="new.py", content="x = 1\n")
        edit = ReplaceTextIntent("bump", "new.py", 0, 0, "x = 1", "x = 2")

        forward = schedule([create, edit], store)
        backward = schedule([edit, create], store)
        assert [i.intent_id for i in forward.ordered] == ["born", "bump"]
        assert [i.intent_id for i in backward.ordered] == ["born", "bump"]

        reversed_run = run_batch([edit, create], store, tx_store=_tx(tmp_path))
        assert reversed_run.dropped == ()
        assert reversed_run.store.read_file("new.py") == b"x = 2\n"

    def test_a_deleted_file_orphans_a_pending_intent_that_declares_it(
        self, project, tmp_path
    ):
        root, store = project({"mod.py": MOD, "doomed.py": "y = 1\n"})
        delete = _DeleteFileIntent("kill", path="doomed.py")
        # Explicitly ordered after the deletion so the drop is the *remap*
        # path, not a scheduling refusal.
        edit = ReplaceTextIntent(
            "bump", "doomed.py", 0, 0, "y = 1", "y = 2", deps={"kill"}
        )

        result = run_batch([delete, edit], store, tx_store=_tx(tmp_path))

        assert [e.intent.intent_id for e in result.executed] == ["kill"]
        (dropped,) = result.dropped
        assert dropped.intent.intent_id == "bump"
        assert dropped.reason is DropReason.ORPHANED
        assert "doomed.py" in dropped.detail
        assert result.store.deleted_files() == ["doomed.py"]
        assert result.store.load("doomed.py") is None
        assert result.effect.files_deleted == frozenset({"doomed.py"})
        assert (root / "doomed.py").exists()  # the real file is untouched

    def test_a_deleted_file_runs_after_its_readers(self, project, tmp_path):
        root, store = project({"mod.py": MOD, "doomed.py": "y = 1\n"})
        delete = _DeleteFileIntent("kill", path="doomed.py")
        edit = ReplaceTextIntent("bump", "doomed.py", 0, 0, "y = 1", "y = 2")

        # Rule 1b orders the reader first whichever way the pair arrives, so
        # the edit lands and only then does the file die.
        for order in ([delete, edit], [edit, delete]):
            assert [i.intent_id for i in schedule(order, store).ordered] == [
                "bump",
                "kill",
            ]

        result = run_batch([delete, edit], store, tx_store=_tx(tmp_path))
        assert [e.intent.intent_id for e in result.executed] == ["bump", "kill"]
        assert result.dropped == ()

    def test_a_deleted_file_orphans_a_symbol_anchored_intent(
        self, project, tmp_path
    ):
        # The rule has to be reachable for the intents that actually make up
        # a batch. Eight of the eleven builtin kinds anchor on a `symbol_id`
        # and derive their footprint by *resolving* it, so a footprint taken
        # after the deletion comes back empty and the intent drops with an
        # opaque "Symbol not found" instead of a machine-readable orphan.
        root, store = project(
            {"mod.py": MOD, "doomed.py": "def g():\n    return 2\n"}
        )
        delete = _DeleteFileIntent("kill", path="doomed.py")
        rename = RenameIntent(
            "ren", symbol_id="doomed:g", new_name="h", deps={"kill"}
        )

        result = run_batch([delete, rename], store, tx_store=_tx(tmp_path))

        assert [e.intent.intent_id for e in result.executed] == ["kill"]
        (dropped,) = result.dropped
        assert dropped.intent.intent_id == "ren"
        assert dropped.reason is DropReason.ORPHANED
        assert dropped.detail == "file 'doomed.py' was deleted by intent 'kill'"

    def test_a_deleted_file_does_not_orphan_a_mere_reader(self, project, tmp_path):
        # The counterpart refusal: footprints over-approximate, so keying the
        # orphan rule on `reads_files` would drop a rename whose importer list
        # merely happened to include the dead file. It is not orphaned — it
        # simply has one fewer importer to rewrite.
        root, store = project(
            {
                "mod.py": "def f():\n    return 1\n",
                "user.py": "from mod import f\n\nf()\n",
            }
        )
        delete = _DeleteFileIntent("kill", path="user.py")
        rename = RenameIntent(
            "ren", symbol_id="mod:f", new_name="g", deps={"kill"}
        )
        # The rename really does declare the doomed file: that is what makes
        # this the case a reads_files-keyed rule gets wrong.
        assert "user.py" in rename.footprint(store).reads_files

        result = run_batch([delete, rename], store, tx_store=_tx(tmp_path))

        assert result.dropped == ()
        assert [e.intent.intent_id for e in result.executed] == ["kill", "ren"]
        assert result.store.read_file("mod.py") == b"def g():\n    return 1\n"

    def test_two_file_creating_intents_order_by_whose_newborn_is_touched(
        self, project, tmp_path
    ):
        # PR3's headline batch shape: two intents that each create a
        # destination module, where one's importer rewrite lands in the
        # other's new module. Gating the birth-ordering edge on "exactly one
        # side creates anything" disabled it for this pair entirely, and the
        # tie-break then scheduled the editor first — a deterministic drop in
        # *both* submission orders against a satisfiable schedule.
        root, store = project({"mod.py": MOD})
        maker = _CreateAndEditIntent(
            "a", path="A.py", content="a = 1\n", edits_path="B.py"
        )
        other = _CreateAndEditIntent(
            "b", path="B.py", content="b = 1\n", edits_path="mod.py"
        )

        for order in ([maker, other], [other, maker]):
            assert [i.intent_id for i in schedule(order, store).ordered] == [
                "b",
                "a",
            ]
            result = run_batch(order, store, tx_store=_tx(tmp_path))
            assert result.dropped == ()
            assert [e.intent.intent_id for e in result.executed] == ["b", "a"]
            assert result.store.read_file("B.py") == b"# touched by a\nb = 1\n"
            assert result.effect.files_created == frozenset({"A.py", "B.py"})

    def test_two_intents_creating_one_path_drop_the_later_submitted(
        self, project, tmp_path
    ):
        root, store = project({"mod.py": MOD})
        first = _CreateFileIntent("alpha", path="new.py", content="a = 1\n")
        second = _CreateFileIntent("beta", path="new.py", content="b = 2\n")

        result = run_batch([first, second], store, tx_store=_tx(tmp_path))
        assert [e.intent.intent_id for e in result.executed] == ["alpha"]
        (dropped,) = result.dropped
        assert dropped.intent.intent_id == "beta"
        assert dropped.reason is DropReason.CONFLICT_DROPPED
        assert "both create 'new.py'" in dropped.detail
        assert result.store.read_file("new.py") == b"a = 1\n"

        # Submission order — and only submission order — decides which one
        # survives, exactly as for two renames of one symbol.
        swapped = run_batch([second, first], store, tx_store=_tx(tmp_path))
        assert [e.intent.intent_id for e in swapped.executed] == ["beta"]
        assert swapped.dropped[0].intent.intent_id == "alpha"

    def test_a_birth_onto_an_existing_path_is_a_machine_readable_drop(
        self, project, tmp_path
    ):
        root, store = project({"mod.py": MOD})
        clash = _CreateFileIntent("clash", path="mod.py", content="x = 1\n")

        result = run_batch([clash], store, tx_store=_tx(tmp_path))
        assert result.executed == ()
        (dropped,) = result.dropped
        assert dropped.reason is DropReason.PRECONDITION_FAILED
        assert dropped.code == "file-already-exists"
        assert "mod.py" in dropped.detail
        # The overlay is untouched: the refusal precedes every write.
        assert result.store.overlaid_files() == []
        assert result.store.read_file("mod.py") == MOD.encode()

    def test_an_edit_against_a_missing_file_drops_instead_of_crashing(
        self, project, tmp_path
    ):
        root, store = project({"mod.py": MOD})
        ghost = _EditMissingFileIntent("ghost", path="nowhere.py")

        # The bug this replaces was a FileNotFoundError escaping run_batch
        # (and the batch CLI's JSON error envelope) as a traceback.
        result = run_batch([ghost], store, tx_store=_tx(tmp_path))
        assert result.executed == ()
        (dropped,) = result.dropped
        assert dropped.reason is DropReason.PRECONDITION_FAILED
        assert dropped.code == "file-missing"
        assert "nowhere.py" in dropped.detail
        assert result.store.overlaid_files() == []

    def test_a_death_of_a_missing_file_drops_the_same_way(self, project, tmp_path):
        root, store = project({"mod.py": MOD})
        ghost = _DeleteFileIntent("ghost", path="nowhere.py")

        result = run_batch([ghost], store, tx_store=_tx(tmp_path))
        (dropped,) = result.dropped
        assert dropped.reason is DropReason.PRECONDITION_FAILED
        assert dropped.code == "file-missing"
        assert result.store.deleted_files() == []


# ---------------------------------------------------------------------------
# Flattening: authorized emission, proven through the real applier
# ---------------------------------------------------------------------------


class TestFlattenLifecycleEntries:
    def test_create_and_delete_round_trip_through_the_applier(
        self, project, tmp_path
    ):
        root, store = project({"mod.py": MOD, "doomed.py": "y = 1\n"})
        before = _snapshot(root)
        intents = [
            _CreateFileIntent("born", path="new.py", content="x = 1\n"),
            _DeleteFileIntent("kill", path="doomed.py"),
            ReplaceTextIntent("bump", "mod.py", 0, 0, "x = 1", "x = 2"),
        ]
        result = run_batch(intents, store, tx_store=_tx(tmp_path))
        assert result.dropped == ()

        flat = flatten_batch(result, store)
        assert [entry.path for entry in flat.creates] == ["new.py"]
        assert [entry.path for entry in flat.deletes] == ["doomed.py"]
        assert [entry.file for entry in flat.edits] == ["mod.py"]
        assert flat.creates[0].content_hash == hashlib.sha256(b"x = 1\n").hexdigest()
        assert flat.deletes[0].file_hash == hashlib.sha256(b"y = 1\n").hexdigest()

        tx_store = TransactionStore(root)
        tx_store.save(
            flat.header, flat.edits, creates=flat.creates, deletes=flat.deletes
        )
        applier = TransactionApplier(store, tx_store)

        applied = applier.apply(flat.header.tx_id)
        assert applied["status"] == "applied"
        assert applied["files_created"] == ["new.py"]
        assert applied["files_deleted"] == ["doomed.py"]
        assert (root / "new.py").read_bytes() == b"x = 1\n"
        assert not (root / "doomed.py").exists()
        assert (root / "mod.py").read_text() == MOD.replace("x = 1", "x = 2")
        # Index hygiene both ways: the newborn is indexed, the corpse is not.
        assert store.load("new.py") is not None
        assert store.load("doomed.py") is None

        rolled = applier.rollback(flat.header.tx_id)
        assert rolled["status"] == "rolled_back"
        assert _sources(_snapshot(root)) == _sources(before)
        assert not (root / "new.py").exists()
        assert (root / "doomed.py").read_bytes() == b"y = 1\n"
        assert store.load("new.py") is None
        assert store.load("doomed.py") is not None

    def test_an_undeclared_birth_still_raises_flatten_error(self, project, tmp_path):
        root, store = project({"mod.py": MOD})
        sneaky = _UndeclaredCreateIntent("sneaky", path="new.py", content="x = 1\n")

        result = run_batch([sneaky], store, tx_store=_tx(tmp_path))
        assert len(result.executed) == 1
        assert result.effect.files_created == frozenset()
        with pytest.raises(FlattenError, match="created"):
            flatten_batch(result, store)

    def test_delete_then_recreate_collapses_to_a_plain_edit(self, project, tmp_path):
        root, store = project({"mod.py": MOD, "swap.py": "y = 1\n"})
        intents = [
            _DeleteFileIntent("kill", path="swap.py"),
            _CreateFileIntent(
                "reborn", path="swap.py", content="y = 2\n", deps={"kill"}
            ),
        ]
        result = run_batch(intents, store, tx_store=_tx(tmp_path))
        assert result.dropped == ()
        # The algebra cancels the pair, and so does the overlay's own record.
        assert result.effect.files_created == frozenset()
        assert result.effect.files_deleted == frozenset()
        assert result.store.deleted_files() == []

        flat = flatten_batch(result, store)
        assert flat.creates == []
        assert flat.deletes == []
        (edit,) = flat.edits
        assert edit.file == "swap.py"
        assert edit.old == "y = 1\n"
        assert edit.new == "y = 2\n"

    def test_create_then_delete_nets_to_nothing(self, project, tmp_path):
        root, store = project({"mod.py": MOD})
        intents = [
            _CreateFileIntent("born", path="ghost.py", content="z = 1\n"),
            _DeleteFileIntent("kill", path="ghost.py", deps={"born"}),
        ]
        result = run_batch(intents, store, tx_store=_tx(tmp_path))
        assert result.dropped == ()
        assert result.effect.files_created == frozenset()
        assert result.effect.files_deleted == frozenset()

        flat = flatten_batch(result, store)
        assert (flat.edits, flat.creates, flat.deletes) == ([], [], [])

    def test_the_seam_emits_entries_for_the_sets_it_is_handed(self, project):
        # flatten_store's authorization is a parameter, not something derived
        # from a batch — the property ITEM B's loop depends on.
        root, store = project({"mod.py": MOD, "doomed.py": "y = 1\n"})
        overlay = OverlayIndexStore(store)
        overlay.write_file("new.py", b"x = 1\n")
        overlay.delete_file("doomed.py")

        flat = flatten_store(
            overlay,
            store,
            operation="batch",
            authorized_created=frozenset({"new.py"}),
            authorized_deleted=frozenset({"doomed.py"}),
        )
        assert [entry.path for entry in flat.creates] == ["new.py"]
        assert [entry.path for entry in flat.deletes] == ["doomed.py"]
        assert (flat.header.operation, flat.edits) == ("batch", [])

        # The 2-element tuple-compat shim is gone (TASK-134): a flattened
        # transaction is attribute-access only, so a destructure that could
        # only ever have seen header and edits — and would have persisted a
        # transaction whose edits assume files it never creates — no longer
        # type-checks at runtime.
        with pytest.raises(TypeError):
            _header, _edits = flat
        with pytest.raises(TypeError):
            flat[0]
