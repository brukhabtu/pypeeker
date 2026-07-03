"""Refactor intents: transforms as semantic anchors + declared footprints/effects.

A composite plan (TASK-88) is a list of :class:`Intent` objects — transform
kind, semantic anchor (symbol ids, file positions), options — not byte edits.
Byte edits go stale the moment another intent runs; anchors survive, because
each executed intent declares an :class:`~pypeeker.refactor.footprint.Effect`
and every pending intent is rewritten through it. The scheduler consumes
three capabilities on every intent:

* :meth:`Intent.footprint` — the declared read/write sets used for pure
  conflict detection (:meth:`~pypeeker.refactor.footprint.Footprint.conflicts_with`).
  Footprints over-approximate: a superset costs scheduling parallelism, a
  subset costs correctness, so each implementation leans conservative.
* :meth:`Intent.predicted_effect` — what executing this intent will do to
  the world's names, *predicted from the anchor* (the real effect of a rename
  is derivable from anchor + new name; extract/inline declare conservative
  file-level effects — see each intent's docstring).
* :meth:`Intent.remap` — rewrite this intent's anchors through another
  intent's effect: exact id substitution or prefix descent for renames, an
  :class:`OrphanedIntent` with a machine-readable :class:`OrphanReason` when
  the anchor was deleted.

The concrete intents wrap the four existing planners
(:class:`~pypeeker.refactor.planner.RenamePlanner`,
:class:`~pypeeker.refactor.extract.ExtractVariablePlanner`,
:class:`~pypeeker.refactor.extract.ExtractMethodPlanner`,
:class:`~pypeeker.refactor.inline.InlineVariablePlanner`) *thinly*: an intent
never executes a plan — it carries the parameters a planner needs, and the
scheduler constructs the planner against the current (overlay) store at
materialization time. ``footprint``/``predicted_effect`` accept that store
for the same reason: declared sets must reflect the world the intent will
run against, not the world at intent-creation time. Any
:class:`~pypeeker.storage.IndexStore`-compatible store works, including
:class:`~pypeeker.storage.overlay.OverlayIndexStore`.

Layering note (:class:`FixIntent`): the TASK-82 Fix protocol lives in
``pypeeker.check.fixes``, but the import boundaries allow ``refactor`` to
import [adapters, analysis, binder, models, paths, project, query, storage] —
**not** ``check`` (rules carry fixes; fixes ride down into refactor-land, not
the other way). :class:`FixIntent` therefore wraps the fix *structurally*:
:class:`PlannableFix` is a local structural :class:`~typing.Protocol`
mirroring ``check.fixes.Fix`` (``fix_id`` / ``description`` /
``plan(store)``), so any conforming object — a real check fix or a test
stub — plugs in without inverting the layering.
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Protocol, runtime_checkable

from pypeeker.models.symbol_id import module_of
from pypeeker.models.symbols import SymbolKind
from pypeeker.query import SemanticQueryEngine
from pypeeker.refactor.footprint import Effect, Footprint, replace_leaf_name

if TYPE_CHECKING:
    from pypeeker.models.symbols import Symbol
    from pypeeker.storage import IndexStore


class OrphanReason(str, Enum):
    """Machine-readable reason an intent could not survive a remap.

    * ``ANCHOR_DELETED`` — the effect deleted the intent's anchor symbol
      (exactly or via a prefix: deleting ``m:Foo`` orphans an intent anchored
      at ``m:Foo.method``).
    """

    ANCHOR_DELETED = "anchor-deleted"


@dataclass(frozen=True)
class OrphanedIntent:
    """An intent whose anchor no longer exists after an effect.

    Returned by :meth:`Intent.remap` instead of a rewritten intent; the
    scheduler reports it (skip-and-report) or aborts (all-or-nothing).
    ``intent`` is the *pre-remap* intent, so the report can name the original
    anchor; ``reason`` is machine-readable, ``detail`` human-readable.
    """

    intent: "Intent"
    reason: OrphanReason
    detail: str = ""


@runtime_checkable
class PlannableFix(Protocol):
    """Structural mirror of ``pypeeker.check.fixes.Fix`` (no check import).

    ``refactor`` may not import ``check`` (see the module docstring), so
    :class:`FixIntent` duck-types its payload: any object exposing a stable
    ``fix_id``, a human-readable ``description``, and ``plan(store)``
    returning either a plan-shaped object carrying an ``edits`` list of
    :class:`~pypeeker.models.transaction.EditEntry`, or a decline-shaped
    object without an ``edits`` attribute (``check.fixes.FixPlan`` /
    ``FixDeclined`` satisfy these shapes by construction).
    """

    @property
    def fix_id(self) -> str:
        """Stable identifier for this fix (e.g. ``"prefer-tuple:listify"``)."""
        ...

    @property
    def description(self) -> str:
        """One-line human-readable summary of what applying the fix does."""
        ...

    def plan(self, store: "IndexStore") -> object:
        """Produce edits valid for the *current* file state, or decline."""
        ...


@dataclass(frozen=True)
class Intent(ABC):
    """A transform anchored semantically, schedulable by footprint and effect.

    ``intent_id`` is the stable handle other intents' ``deps`` reference and
    scheduler reports name; ``deps`` declares *explicit* ordering edges
    (intent ids that must execute first) on top of whatever ordering footprint
    conflicts impose. Concrete intents add their anchor parameters as frozen
    dataclass fields, so every intent is hashable and ``remap`` can rewrite
    anchors via :func:`dataclasses.replace`.
    """

    intent_id: str
    deps: frozenset[str] = field(default=frozenset(), kw_only=True)

    kind: ClassVar[str]
    """Stable transform kind (``"rename"``, ``"inline-variable"``, ...)."""

    def __post_init__(self) -> None:
        """Normalise ``deps`` to a frozenset so any iterable is accepted."""
        object.__setattr__(self, "deps", frozenset(self.deps))

    @abstractmethod
    def footprint(self, store: "IndexStore") -> Footprint:
        """Declared reads/writes against the current state seen through ``store``."""

    @abstractmethod
    def predicted_effect(self, store: "IndexStore") -> Effect:
        """What executing this intent is predicted to do to the world's names."""

    @abstractmethod
    def remap(self, effect: Effect) -> "Intent | OrphanedIntent":
        """This intent with anchors rewritten through ``effect``, or an orphan."""


def _resolve_unique(engine: SemanticQueryEngine, symbol_id: str) -> "Symbol | None":
    """The symbol ``symbol_id`` resolves to, or ``None`` unless exactly one match.

    Footprints must be computable even when the anchor does not (or no
    longer) resolves — preconditions re-checked at materialization time are
    what *reject* such intents — so resolution failures degrade the footprint
    rather than raising.
    """
    results = engine.find_symbol(symbol_id)
    return results[0] if len(results) == 1 else None


def _remap_symbol_anchor(
    intent: Intent, effect: Effect, *, describe: str
) -> "Intent | OrphanedIntent":
    """Shared remap for intents anchored on a single ``symbol_id`` field.

    Applies :meth:`Effect.remap_id` (exact match or prefix descent) to the
    intent's ``symbol_id``; a deleted anchor yields an :class:`OrphanedIntent`
    with :attr:`OrphanReason.ANCHOR_DELETED`. Unchanged anchors return the
    intent itself (intents are frozen, so sharing is safe).
    """
    anchor: str = intent.symbol_id  # type: ignore[attr-defined]
    target = effect.remap_id(anchor)
    if target is None:
        return OrphanedIntent(
            intent,
            OrphanReason.ANCHOR_DELETED,
            f"{describe} anchor '{anchor}' was deleted",
        )
    if target == anchor:
        return intent
    return dataclasses.replace(intent, symbol_id=target)


@dataclass(frozen=True)
class RenameIntent(Intent):
    """Rename the symbol ``symbol_id`` to ``new_name``.

    Wraps :class:`~pypeeker.refactor.planner.RenamePlanner` parameters; the
    option flags match :meth:`RenamePlanner.plan` keyword-for-keyword.

    Footprint: writes the anchor id as a prefix (renaming ``m:Foo`` affects
    every ``m:Foo.*``); reads and writes every file the rename may touch —
    the definition's file, importer files, and reference files, a deliberate
    superset of the planner's export-gated edit set. A METHOD anchor also
    reads the global ``"hierarchy"`` fact (the override-safety check consults
    the class hierarchy, which any code write can invalidate).

    Predicted effect: ``{anchor: anchor-with-new-leaf}`` — exact for the
    anchor itself, and descendants follow by prefix descent in
    :meth:`Effect.remap_id`, so one entry covers ``m:Foo.method`` etc. With
    ``include_file``, predicts the planner's file rename (stem matches the
    symbol name case-insensitively).
    """

    symbol_id: str
    new_name: str
    include_file: bool = field(default=False, kw_only=True)
    include_exports: bool = field(default=False, kw_only=True)
    include_receivers: bool = field(default=False, kw_only=True)
    keep_export: bool = field(default=False, kw_only=True)
    allow_override_rename: bool = field(default=False, kw_only=True)

    kind: ClassVar[str] = "rename"

    def footprint(self, store: "IndexStore") -> Footprint:
        """Symbol-prefix write on the anchor plus file writes for all touchpoints."""
        engine = SemanticQueryEngine(store)
        symbol = _resolve_unique(engine, self.symbol_id)
        files: set[str] = set()
        facts: set[str] = set()
        if symbol is not None:
            files.add(symbol.location.file_path)
            importers = engine.find_importers(symbol.symbol_id)
            files.update(imp.location.file_path for imp in importers)
            for binding_id in {symbol.symbol_id, *(i.symbol_id for i in importers)}:
                files.update(
                    ref.location.file_path
                    for ref in engine.references_to_binding(binding_id)
                )
            if self.include_receivers:
                files.update(
                    ref.location.file_path
                    for ref in engine.references_to_definition(
                        symbol.symbol_id, declared_only=True
                    )
                )
            if symbol.kind is SymbolKind.METHOD:
                facts.add("hierarchy")
        return Footprint(
            writes_symbols={self.symbol_id},
            reads_files=files,
            writes_files=files,
            reads_facts=facts,
        )

    def predicted_effect(self, store: "IndexStore") -> Effect:
        """An id substitution derived from the anchor and the new name."""
        files_renamed: dict[str, str] = {}
        if self.include_file:
            engine = SemanticQueryEngine(store)
            symbol = _resolve_unique(engine, self.symbol_id)
            if symbol is not None:
                rename = _predict_file_rename(
                    symbol.location.file_path, symbol.name, self.new_name
                )
                if rename is not None:
                    files_renamed[rename[0]] = rename[1]
        return Effect(
            renamed={self.symbol_id: replace_leaf_name(self.symbol_id, self.new_name)},
            files_written=self.footprint(store).writes_files,
            files_renamed=files_renamed,
        )

    def remap(self, effect: Effect) -> "Intent | OrphanedIntent":
        """Follow renames of the anchor (rename-vs-rename composes); orphan on delete."""
        return _remap_symbol_anchor(self, effect, describe="rename")


def _predict_file_rename(
    file_path: str, symbol_name: str, new_name: str
) -> tuple[str, str] | None:
    """The (old, new) file rename ``--include-file`` would perform, if any.

    Mirrors ``RenamePlanner._check_file_rename``: only when the file stem
    matches the symbol name case-insensitively; the new file is the
    lowercased new name.
    """
    if Path(file_path).stem.lower() != symbol_name.lower():
        return None
    parent = Path(file_path).parent
    new_file = new_name.lower() + ".py"
    new_path = new_file if parent == Path(".") else str(parent / new_file)
    return file_path, new_path


@dataclass(frozen=True)
class InlineVariableIntent(Intent):
    """Inline the function-local variable ``symbol_id`` into its uses.

    Wraps :class:`~pypeeker.refactor.inline.InlineVariablePlanner` (which
    takes only the symbol id).

    Footprint: writes the variable's id (the binding is deleted) and its
    defining file. Reads the scoped fact ``"purity:<symbol_id>"`` — the
    multi-use safety check analyses the assigned value's purity, so a write
    inside the variable's enclosing scope invalidates the intent. (Honest
    limitation: purity of the value also depends on functions it *calls*;
    a write to those elsewhere is not captured. Materialization-time
    precondition re-checks are the backstop.)

    Predicted effect: the variable's id is deleted and its file written.
    """

    symbol_id: str

    kind: ClassVar[str] = "inline-variable"

    def footprint(self, store: "IndexStore") -> Footprint:
        """Symbol write on the variable plus a write of its defining file."""
        engine = SemanticQueryEngine(store)
        symbol = _resolve_unique(engine, self.symbol_id)
        files = {symbol.location.file_path} if symbol is not None else set()
        return Footprint(
            writes_symbols={self.symbol_id},
            reads_files=files,
            writes_files=files,
            reads_facts={f"purity:{self.symbol_id}"},
        )

    def predicted_effect(self, store: "IndexStore") -> Effect:
        """The variable's binding disappears; its file is rewritten."""
        return Effect(
            deleted={self.symbol_id},
            files_written=self.footprint(store).writes_files,
        )

    def remap(self, effect: Effect) -> "Intent | OrphanedIntent":
        """Follow renames of the variable; orphan when it was deleted."""
        return _remap_symbol_anchor(self, effect, describe="inline-variable")


@dataclass(frozen=True)
class DeleteSymbolIntent(Intent):
    """Delete the symbol ``symbol_id`` (definition removal).

    No standalone delete planner exists yet; this intent is the data-model
    expression of a delete-style transform (e.g. the deletion half of an
    inline-then-delete-import chain) so the scheduler can order it against
    renames and reads. Footprint and effect are conservative: a symbol-prefix
    write on the target plus a write of its defining file.

    Remapping covers the epic's rename-vs-delete case: a delete whose target
    a prior rename moved follows the substitution to the new id; a delete
    whose target was already deleted is orphaned.
    """

    symbol_id: str

    kind: ClassVar[str] = "delete-symbol"

    def footprint(self, store: "IndexStore") -> Footprint:
        """Symbol-prefix write on the target plus a write of its defining file."""
        engine = SemanticQueryEngine(store)
        symbol = _resolve_unique(engine, self.symbol_id)
        files = {symbol.location.file_path} if symbol is not None else set()
        return Footprint(
            writes_symbols={self.symbol_id},
            reads_files=files,
            writes_files=files,
        )

    def predicted_effect(self, store: "IndexStore") -> Effect:
        """The target id (and, by prefix, its descendants) is deleted."""
        return Effect(
            deleted={self.symbol_id},
            files_written=self.footprint(store).writes_files,
        )

    def remap(self, effect: Effect) -> "Intent | OrphanedIntent":
        """Follow renames of the target (rename-vs-delete); orphan on delete."""
        return _remap_symbol_anchor(self, effect, describe="delete-symbol")


@dataclass(frozen=True)
class ExtractVariableIntent(Intent):
    """Extract the expression at ``start``..``end`` in ``file_path`` into ``new_name``.

    Wraps :class:`~pypeeker.refactor.extract.ExtractVariablePlanner`:
    ``start``/``end`` are 0-indexed ``(line, column)`` positions, matching
    :meth:`ExtractVariablePlanner.plan`.

    Footprint and effect are deliberately file-level: the transform is
    file-local, and the created variable's precise id would need scope
    analysis at the anchor (which enclosing function, shadow ordinals), so
    ``created`` is left empty rather than guessed. Position anchors are not
    remapped through effects — a prior write to the same file conflicts via
    the file footprint, and the planner's preconditions re-validate the
    positions at materialization time.
    """

    file_path: str
    start: tuple[int, int]
    end: tuple[int, int]
    new_name: str

    kind: ClassVar[str] = "extract-variable"

    def footprint(self, store: "IndexStore") -> Footprint:
        """Reads and writes the anchored file only (the transform is file-local)."""
        return Footprint(reads_files={self.file_path}, writes_files={self.file_path})

    def predicted_effect(self, store: "IndexStore") -> Effect:
        """Conservative file-level effect (created variable id not predicted)."""
        return Effect(files_written={self.file_path})

    def remap(self, effect: Effect) -> "Intent | OrphanedIntent":
        """Follow file renames of the anchored path; positions are left as-is."""
        new_path = effect.remap_file(self.file_path)
        if new_path == self.file_path:
            return self
        return dataclasses.replace(self, file_path=new_path)


@dataclass(frozen=True)
class ExtractMethodIntent(Intent):
    """Extract lines ``start_line``..``end_line`` of ``file_path`` into ``new_name``.

    Wraps :class:`~pypeeker.refactor.extract.ExtractMethodPlanner`: lines are
    0-indexed and inclusive, matching :meth:`ExtractMethodPlanner.plan`.

    Footprint is file-level (the v1 transform is file-local). The predicted
    effect names the created top-level function ``<module>:<new_name>`` when
    the module path is derivable from the file's index (extract-method v1
    only creates top-level functions, so the id shape is known); without an
    index it degrades to the file-level effect. Position anchors are not
    remapped — see :class:`ExtractVariableIntent`.
    """

    file_path: str
    start_line: int
    end_line: int
    new_name: str

    kind: ClassVar[str] = "extract-method"

    def footprint(self, store: "IndexStore") -> Footprint:
        """Reads and writes the anchored file only (the transform is file-local)."""
        return Footprint(reads_files={self.file_path}, writes_files={self.file_path})

    def predicted_effect(self, store: "IndexStore") -> Effect:
        """File write plus the created ``<module>:<new_name>`` id when derivable."""
        created: set[str] = set()
        index = store.load(self.file_path)
        if index is not None and index.symbols:
            module = module_of(index.symbols[0].symbol_id)
            created.add(f"{module}:{self.new_name}")
        return Effect(created=created, files_written={self.file_path})

    def remap(self, effect: Effect) -> "Intent | OrphanedIntent":
        """Follow file renames of the anchored path; line anchors are left as-is."""
        new_path = effect.remap_file(self.file_path)
        if new_path == self.file_path:
            return self
        return dataclasses.replace(self, file_path=new_path)


@dataclass(frozen=True)
class FixIntent(Intent):
    """A TASK-82 fix-protocol edit as an intent (kind ``"edit"``).

    Carries any :class:`PlannableFix`-shaped object (see the protocol and the
    module docstring on layering — ``check`` is never imported). The fix is
    the anchor: by the Fix contract it is replannable, re-anchoring against
    current bytes inside ``plan(store)`` and declining when its anchor no
    longer holds, so :meth:`remap` is the identity — symbol substitutions
    cannot be applied to an opaque fix, and a renamed/removed target file
    surfaces as a decline at materialization time instead.

    Footprint and effect come from planning the fix against ``store``: the
    files its edits touch are read and written. A declined fix (an object
    without an ``edits`` attribute) yields an empty footprint/effect — the
    scheduler's guarded re-validation, not conflict detection, is what
    reports the decline.
    """

    fix: PlannableFix

    kind: ClassVar[str] = "edit"

    def _edit_files(self, store: "IndexStore") -> frozenset[str]:
        """Files touched by planning the fix now; empty when the fix declines."""
        result = self.fix.plan(store)
        edits = getattr(result, "edits", None)
        if edits is None:
            return frozenset()
        return frozenset(edit.file for edit in edits)

    def footprint(self, store: "IndexStore") -> Footprint:
        """Reads and writes exactly the files the fix's planned edits touch."""
        files = self._edit_files(store)
        return Footprint(reads_files=files, writes_files=files)

    def predicted_effect(self, store: "IndexStore") -> Effect:
        """File-level writes only — fix edits never rename or delete symbols."""
        return Effect(files_written=self._edit_files(store))

    def remap(self, effect: Effect) -> "Intent | OrphanedIntent":
        """Identity: fixes re-anchor themselves at plan time (Fix contract)."""
        return self


__all__ = [
    "OrphanReason",
    "OrphanedIntent",
    "PlannableFix",
    "Intent",
    "RenameIntent",
    "InlineVariableIntent",
    "DeleteSymbolIntent",
    "ExtractVariableIntent",
    "ExtractMethodIntent",
    "FixIntent",
]
