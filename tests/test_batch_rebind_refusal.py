"""The simulation's re-bind refuses instead of crashing (TASK-141).

The fourth raw-decode crash was ``DeleteSymbolPlanner``'s span decode, closed
by a span-scoped ``SourceIsUtf8`` guard. Closing the *family* turned up a
fifth site the sweep had first written off as unreachable:
:func:`~pypeeker.refactor.simulate.rebind_source`, reached through
:func:`~pypeeker.refactor.batch.apply_to_overlay` by ``check --fix
--fix-until-clean``'s fixpoint and through ``_apply_to_overlay`` by every
multi-intent ``run_batch``.

What makes it a *separate* site rather than a duplicate of the planner guards
is that no guard on the edit could have caught it. An edit is verified
against, and records, only the span it rewrites; the bytes it leaves alone
are not its business. Here a latin-1 string literal sits legally in the file
as an expression statement — it binds fine, and ``pypeeker index`` reports no
error — until deleting the dead ``def`` above it *promotes* it into module-
docstring position, a node ``binder._module_docstring`` decodes and the
pre-image never did. "These bytes bound once" therefore does not survive a
splice, which is the whole reason the check has to be an actual bind.

Nothing here modifies an existing test: the module is new, and the
``rebind_final=False`` opt-out proven in ``test_submit_one_path.py`` is
re-checked below rather than rewritten.
"""

from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path
from typing import ClassVar

import pytest

from pypeeker.adapters import PythonAdapter
from pypeeker.binder.binder import bind
from pypeeker.intents import Effect, Footprint, Intent
from pypeeker.models import EditEntry, EditOp
from pypeeker.refactor.batch import (
    DropReason,
    OverlayApplyError,
    apply_to_overlay,
    run_batch,
)
from pypeeker.refactor import registry
from pypeeker.refactor.registry import Materialized, register_planner
from pypeeker.storage import IndexStore, OverlayIndexStore, TransactionStore

# The dead definition, then two blank lines, then a latin-1 literal. As
# written the literal is an expression statement (the binder never decodes
# it); delete the definition and it becomes the module docstring (which the
# binder does decode).
PROMOTES_ON_DELETE = (
    b"def _dead():\n"
    b"    return 1\n"
    b"\n"
    b"\n"
    b'"caf\xe9"\n'
)

# The control: the same undecodable bytes in a *comment*, which no binder
# node ever covers. The identical deletion must still go through, or the
# guard has become the whole-file refusal TASK-136/139 ruled out.
STAYS_UNBOUND = (
    b"def _dead():\n"
    b"    return 1\n"
    b"\n"
    b"\n"
    b"# caf\xe9\n"
    b"KEEP = 1\n"
)

_DEAD_DEF_END = PROMOTES_ON_DELETE.index(b'"caf')
_DEAD_DEF_TEXT = "def _dead():\n    return 1\n\n\n"


@dataclasses.dataclass(frozen=True)
class _DropLeadingDefIntent(Intent):
    """Delete the leading ``def _dead(): ...`` block from ``path``."""

    path: str
    end: int

    kind: ClassVar[str] = "test-only:drop-leading-def"

    def footprint(self, store) -> Footprint:
        """Reads and writes the one file it splices."""
        return Footprint(reads_files={self.path}, writes_files={self.path})

    def predicted_effect(self, store) -> Effect:
        """A plain write: no birth, no death, no rename."""
        return Effect(files_written={self.path})

    def remap(self, effect) -> Intent:
        """Identity: a path anchor follows no substitution."""
        return self


@dataclasses.dataclass(frozen=True)
class _AppendIntent(Intent):
    """Append ``text`` to ``path`` — a second intent, to force a re-bind."""

    path: str
    at: int
    text: str

    kind: ClassVar[str] = "test-only:append-line"

    def footprint(self, store) -> Footprint:
        """Reads and writes the one file it splices."""
        return Footprint(reads_files={self.path}, writes_files={self.path})

    def predicted_effect(self, store) -> Effect:
        """A plain write."""
        return Effect(files_written={self.path})

    def remap(self, effect) -> Intent:
        """Identity."""
        return self


@pytest.fixture(autouse=True)
def rebind_kinds():
    """Register the two test-only materializers; unregister them afterwards."""

    @register_planner(_DropLeadingDefIntent.kind)
    def _materialize_drop(intent, store, tx_store):
        return Materialized(
            edits=[
                EditEntry(
                    op=EditOp.DELETE,
                    file=intent.path,
                    start=0,
                    end=intent.end,
                    old=_DEAD_DEF_TEXT,
                    new="",
                    file_hash=store.file_hash(intent.path),
                )
            ]
        )

    @register_planner(_AppendIntent.kind)
    def _materialize_append(intent, store, tx_store):
        return Materialized(
            edits=[
                EditEntry(
                    op=EditOp.INSERT,
                    file=intent.path,
                    start=intent.at,
                    end=intent.at,
                    old="",
                    new=intent.text,
                    file_hash=store.file_hash(intent.path),
                )
            ]
        )

    yield
    for kind in (_DropLeadingDefIntent.kind, _AppendIntent.kind):
        registry._REGISTRY.pop(kind, None)


@pytest.fixture
def raw_project(tmp_path):
    """``{name: bytes} -> (root, store)``: an indexed project over raw bytes."""

    def _setup(files: dict[str, bytes]):
        root = tmp_path / "proj"
        (root / ".pypeeker" / "index").mkdir(parents=True)
        store = IndexStore(root)
        adapter = PythonAdapter()
        for name, source in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(source)
            tree = adapter.parse(source)
            store.save(bind(adapter, name, source, tree.root_node))
        return root, store

    return _setup


def _tx(tmp_path: Path) -> TransactionStore:
    """A scratch transaction store for the simulated re-plans."""
    return TransactionStore(tmp_path / "scratch-tx")


class TestThePreImageReallyBinds:
    """The premise: these bytes index cleanly *before* the splice."""

    def test_the_undecodable_literal_binds_as_an_expression_statement(
        self, raw_project
    ):
        # If this ever fails the fixtures below stop testing what they claim:
        # the point is a file the tool accepts, not one it already rejects.
        _, store = raw_project({"mod.py": PROMOTES_ON_DELETE})
        assert store.load("mod.py") is not None

    def test_the_undecodable_comment_binds_too(self, raw_project):
        _, store = raw_project({"mod.py": STAYS_UNBOUND})
        assert store.load("mod.py") is not None


class TestRunBatchDropsInsteadOfCrashing:
    """A splice whose result cannot bind is a machine-readable drop.

    Each batch pairs the deletion with a second intent on a *different*
    file: two intents means the first one's apply re-binds (``rebind_final``
    only ever spares the last), and separate files keep them unordered by the
    scheduler so the deletion is not re-anchored by a byte conflict — which
    would drop it for the wrong reason.
    """

    OTHER = b"Y = 2\n"

    def _batch(self, source: bytes, end: int, raw_project, tmp_path):
        _, store = raw_project({"mod.py": source, "other.py": self.OTHER})
        return run_batch(
            [
                _DropLeadingDefIntent("drop", path="mod.py", end=end),
                _AppendIntent(
                    "append", path="other.py", at=len(self.OTHER), text="Z = 3\n"
                ),
            ],
            store,
            tx_store=_tx(tmp_path),
        )

    def test_the_intent_drops_with_rebind_failed(self, raw_project, tmp_path):
        result = self._batch(
            PROMOTES_ON_DELETE, _DEAD_DEF_END, raw_project, tmp_path
        )

        (dropped,) = result.dropped
        assert dropped.intent.intent_id == "drop"
        assert dropped.reason is DropReason.PRECONDITION_FAILED
        assert dropped.code == "rebind-failed"
        assert "mod.py" in dropped.detail
        assert "not valid UTF-8" in dropped.detail

    def test_the_refused_intents_bytes_are_not_carried_forward(
        self, raw_project, tmp_path
    ):
        """Refusing in phase 1 leaves no half-applied splice behind."""
        result = self._batch(
            PROMOTES_ON_DELETE, _DEAD_DEF_END, raw_project, tmp_path
        )

        assert result.store.read_file("mod.py") == PROMOTES_ON_DELETE
        assert "mod.py" not in result.store.overlaid_files()
        # ...and the rest of the batch is unharmed by its neighbour's refusal.
        assert [e.intent.intent_id for e in result.executed] == ["append"]
        assert result.store.read_file("other.py") == b"Y = 2\nZ = 3\n"

    def test_an_undecodable_comment_is_not_refused(self, raw_project, tmp_path):
        """The guard is a bind, not a whole-file UTF-8 check."""
        result = self._batch(
            STAYS_UNBOUND, STAYS_UNBOUND.index(b"# caf"), raw_project, tmp_path
        )

        assert result.dropped == ()
        assert result.store.read_file("mod.py") == b"# caf\xe9\nKEEP = 1\n"


class TestApplyToOverlayRefuses:
    """The public seam ``check --fix --fix-until-clean`` drives."""

    @staticmethod
    def _deletion(store) -> Materialized:
        return Materialized(
            edits=[
                EditEntry(
                    op=EditOp.DELETE,
                    file="mod.py",
                    start=0,
                    end=_DEAD_DEF_END,
                    old=_DEAD_DEF_TEXT,
                    new="",
                    file_hash=store.file_hash("mod.py"),
                )
            ]
        )

    def test_it_raises_overlay_apply_error_not_unicode_decode_error(
        self, raw_project
    ):
        _, store = raw_project({"mod.py": PROMOTES_ON_DELETE})
        overlay = OverlayIndexStore(store)

        with pytest.raises(OverlayApplyError) as exc:
            apply_to_overlay(overlay, self._deletion(store))

        assert exc.value.code == "rebind-failed"
        assert "mod.py" in str(exc.value)
        assert "not valid UTF-8" in str(exc.value)

    def test_the_overlay_bytes_are_untouched_by_the_refusal(self, raw_project):
        _, store = raw_project({"mod.py": PROMOTES_ON_DELETE})
        overlay = OverlayIndexStore(store)

        with pytest.raises(OverlayApplyError):
            apply_to_overlay(overlay, self._deletion(store))

        # Nothing was spliced, so `flatten_store` has nothing to diff and the
        # real tree can never be reached from this overlay.
        assert overlay.overlaid_files() == []
        assert overlay.read_file("mod.py") == PROMOTES_ON_DELETE

    def test_a_bindable_splice_still_applies_and_rebinds(self, raw_project):
        _, store = raw_project({"mod.py": STAYS_UNBOUND})
        overlay = OverlayIndexStore(store)

        apply_to_overlay(
            overlay,
            Materialized(
                edits=[
                    EditEntry(
                        op=EditOp.DELETE,
                        file="mod.py",
                        start=0,
                        end=STAYS_UNBOUND.index(b"# caf"),
                        old=_DEAD_DEF_TEXT,
                        new="",
                        file_hash=store.file_hash("mod.py"),
                    )
                ]
            ),
        )

        assert overlay.read_file("mod.py") == b"# caf\xe9\nKEEP = 1\n"
        assert overlay.is_stale("mod.py") is False
        assert [s.name for s in overlay.load("mod.py").symbols if s.name == "KEEP"]


class TestTheOptOutStillSkipsTheBind:
    """``rebind_final=False`` must not acquire a refusal it never had."""

    def test_a_batch_of_one_applies_the_unbindable_splice(
        self, raw_project, tmp_path
    ):
        """Nobody reads the index, so there is nothing to fail closed on.

        This is the configuration ``submit_intent`` (and therefore plain
        ``check --fix``) uses. Its answer to undecodable post-splice bytes
        stays what it was: apply, and let the applier's own re-index report
        ``files_reindex_failed``.
        """
        _, store = raw_project({"mod.py": PROMOTES_ON_DELETE})

        result = run_batch(
            [_DropLeadingDefIntent("drop", path="mod.py", end=_DEAD_DEF_END)],
            store,
            tx_store=_tx(tmp_path),
            rebind_final=False,
        )

        assert result.dropped == ()
        assert [e.intent.intent_id for e in result.executed] == ["drop"]
        assert result.store.read_file("mod.py") == b'"caf\xe9"\n'

    def test_the_same_batch_with_the_rebind_on_refuses(
        self, raw_project, tmp_path
    ):
        """The pair: identical input, and only the re-bind decides."""
        _, store = raw_project({"mod.py": PROMOTES_ON_DELETE})

        result = run_batch(
            [_DropLeadingDefIntent("drop", path="mod.py", end=_DEAD_DEF_END)],
            store,
            tx_store=_tx(tmp_path),
            rebind_final=True,
        )

        assert result.executed == ()
        (dropped,) = result.dropped
        assert dropped.code == "rebind-failed"
        assert result.store.read_file("mod.py") == PROMOTES_ON_DELETE


def test_the_file_hash_helper_is_not_what_this_tests(raw_project):
    """Guard against the fixture silently drifting to decodable bytes."""
    _, store = raw_project({"mod.py": PROMOTES_ON_DELETE})
    assert hashlib.sha256(PROMOTES_ON_DELETE).hexdigest() == store.file_hash(
        "mod.py"
    )
    with pytest.raises(UnicodeDecodeError):
        PROMOTES_ON_DELETE.decode("utf-8")
