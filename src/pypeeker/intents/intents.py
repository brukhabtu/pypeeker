"""Intents: transforms as semantic anchors + declared footprints/effects.

A composite plan (TASK-88) is a list of :class:`Intent` objects — transform
kind, semantic anchor (symbol ids, file positions), options — not byte edits.
Byte edits go stale the moment another intent runs; anchors survive, because
each executed intent declares an :class:`~pypeeker.intents.footprint.Effect`
and every pending intent is rewritten through it. The scheduler consumes
three capabilities on every intent:

* :meth:`Intent.footprint` — the declared read/write sets used for pure
  conflict detection (:meth:`~pypeeker.intents.footprint.Footprint.conflicts_with`).
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
scheduler (in ``refactor``) constructs the planner against the current
(overlay) store at materialization time. ``footprint``/``predicted_effect``
accept that store for the same reason: declared sets must reflect the world
the intent will run against, not the world at intent-creation time. Any
:class:`~pypeeker.storage.IndexStore`-compatible store works, including
:class:`~pypeeker.storage.overlay.OverlayIndexStore`.

Layering note: ``pypeeker.intents`` is a near-leaf — the import boundaries
allow it to import only [models, query, storage] — so both ``check`` and
``refactor`` may depend on it, and it depends on neither. This module never
imports the planners it names above; those live in ``refactor`` and are
constructed by the batch scheduler, not by an intent itself.

Layering note (check remedies): a ``check`` rule attaches the intent that
repairs what it flagged as ``Violation.remedy`` (TASK-124) — ``check``
imports this package, never ``refactor``, so a rule says *what* should
change and only the registered planner behind the intent's ``kind`` knows
*how*. Five kinds exist for that purpose (:class:`DeleteSymbolIntent`,
:class:`RemoveImportIntent`, :class:`RewriteStarImportIntent`,
:class:`TuplifyIntent`, :class:`RenameDocstringParamIntent`); each overrides
:attr:`Intent.description` with the one-line summary ``check --fix``
reports next to the repair. :class:`ReplaceTextIntent` overrides it too but
is not a remedy kind: it is the ported reference *text*-anchored op (see its
class docstring), attached by no rule today.
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import ClassVar

from pypeeker.intents.anchors import Anchor, EdgeAnchor, RangeAnchor, SymbolAnchor
from pypeeker.intents.footprint import EMPTY_EFFECT, Effect, Footprint, replace_leaf_name
from pypeeker.models import Symbol, SymbolKind, leaf_name, module_of, strip_shadow
from pypeeker.query import SemanticQueryEngine
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

    @property
    def description(self) -> str:
        """One-line human-readable summary of what executing this intent does.

        The generic fallback names the kind and the intent id. The five
        check-remedy kinds (see the module docstring) override it with the
        phrasing ``check --fix`` reports next to each repair; nothing else
        reads it today, so the other kinds keep the fallback rather than
        inventing prose no caller consumes.
        """
        return f"{self.kind} '{self.intent_id}'"

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
class ChangeVisibilityIntent(Intent):
    """Change ``symbol_id``'s visibility: demote (``name -> _name``) or
    promote (``_name -> name``), selected by ``direction``.

    Wraps :class:`~pypeeker.refactor.visibility_ops.VisibilityPlanner`
    (``plan_demote``/``plan_promote``) parameters. ``keep_export`` only
    applies to ``"demote"`` and ``add_export`` only to ``"promote"``; the
    field that does not apply to this intent's ``direction`` is simply
    ignored by the materializer (TASK-123).

    Footprint and predicted effect delegate to a :class:`RenameIntent` built
    from the resolved new name (``_name`` for demote, the name with one
    leading underscore stripped for promote) with ``include_exports=True``
    — a deliberate over-approximation: the real visibility op only rewrites
    barrel exports when the symbol is actually barrel-exported (and demote's
    ``keep_export`` skips it even then), but declaring that touchpoint
    unconditionally keeps the footprint conservative, which costs scheduling
    parallelism, never correctness (see the module docstring on
    over-approximation). When the anchor does not resolve, or the direction
    does not apply (e.g. promoting an already-public name), the delegate
    degrades to an unresolved-anchor footprint/effect, matching
    :class:`RenameIntent`'s own degrade-gracefully behaviour; the real
    refusal surfaces at materialization time, where
    :class:`~pypeeker.refactor.visibility_ops.VisibilityPlanner` re-validates
    everything.

    ``promote(..., add_export=pkg)`` additionally writes ``pkg``'s indexed
    ``__init__.py`` and predicts the new barrel binding it creates
    (``pkg:<new_name>``), unioned in on top of the rename delegate. That
    file is *not* generally reachable through the delegate's
    ``find_importers``-derived footprint: :meth:`VisibilityPlanner
    <pypeeker.refactor.visibility_ops.VisibilityPlanner._build_export_edits>`
    refuses ``add_export`` when ``pkg/__init__.py`` already binds the new
    name, so in the normal (accepted) case that ``__init__`` does not import
    the symbol at all, and the delegate rename would omit it — a footprint
    *subset*, not the over-approximation this class promises. The target
    file is located the same way
    :meth:`~pypeeker.refactor.visibility_ops.VisibilityPlanner._package_init_path`
    does — the indexed ``__init__.py`` whose MODULE symbol id equals
    ``pkg`` — without importing ``refactor`` (this package is a near-leaf;
    see the module docstring).
    """

    symbol_id: str
    direction: str
    keep_export: bool = field(default=False, kw_only=True)
    add_export: str | None = field(default=None, kw_only=True)

    kind: ClassVar[str] = "change-visibility"

    def _new_name(self, store: "IndexStore") -> str | None:
        """The name this op would rename to, or ``None`` when unresolvable."""
        engine = SemanticQueryEngine(store)
        symbol = _resolve_unique(engine, self.symbol_id)
        if symbol is None:
            return None
        if self.direction == "demote":
            return "_" + symbol.name
        if self.direction == "promote" and symbol.name.startswith("_"):
            return symbol.name[1:]
        return None

    def _as_rename(self, store: "IndexStore") -> "RenameIntent | None":
        """A conservative :class:`RenameIntent` standing in for footprint/effect."""
        new_name = self._new_name(store)
        if new_name is None:
            return None
        return RenameIntent(
            self.intent_id, self.symbol_id, new_name, include_exports=True
        )

    def _export_target_init_file(self, store: "IndexStore") -> str | None:
        """The ``add_export`` package's indexed ``__init__.py`` path, or ``None``.

        Only applies to ``"promote"``; ``add_export`` is ignored for
        ``"demote"`` (see the class docstring). Mirrors
        :meth:`~pypeeker.refactor.visibility_ops.VisibilityPlanner._package_init_path`
        — a package's ``__init__.py`` is the indexed file whose MODULE symbol
        id equals the dotted package path — without importing ``refactor``.
        """
        if self.direction != "promote" or self.add_export is None:
            return None
        for file_path in store.list_indexed_files():
            if not file_path.endswith("__init__.py"):
                continue
            index = store.load(file_path)
            if index is None:
                continue
            for symbol in index.symbols:
                if symbol.kind is SymbolKind.MODULE:
                    if symbol.symbol_id == self.add_export:
                        return file_path
                    break
        return None

    def footprint(self, store: "IndexStore") -> Footprint:
        """Delegate to the standing-in rename; a bare anchor write when unresolvable.

        When promoting with ``add_export``, also writes the target package's
        ``__init__.py`` (see the class docstring).
        """
        rename = self._as_rename(store)
        if rename is None:
            return Footprint(writes_symbols={self.symbol_id})
        footprint = rename.footprint(store)
        init_file = self._export_target_init_file(store)
        if init_file is not None:
            footprint = dataclasses.replace(
                footprint,
                reads_files=footprint.reads_files | {init_file},
                writes_files=footprint.writes_files | {init_file},
            )
        return footprint

    def predicted_effect(self, store: "IndexStore") -> Effect:
        """Delegate to the standing-in rename; the empty effect when unresolvable.

        When promoting with ``add_export``, also predicts the write to the
        target package's ``__init__.py`` and the barrel binding it creates
        (see the class docstring).
        """
        rename = self._as_rename(store)
        if rename is None:
            return EMPTY_EFFECT
        effect = rename.predicted_effect(store)
        init_file = self._export_target_init_file(store)
        if init_file is not None:
            effect = dataclasses.replace(
                effect,
                created=effect.created | {f"{self.add_export}:{rename.new_name}"},
                files_written=effect.files_written | {init_file},
            )
        return effect

    def remap(self, effect: Effect) -> "Intent | OrphanedIntent":
        """Follow renames of the anchor; orphan when it was deleted."""
        return _remap_symbol_anchor(self, effect, describe="change-visibility")


@dataclass(frozen=True)
class DeleteSymbolIntent(Intent):
    """Delete the symbol ``symbol_id`` (definition removal).

    Wraps the superseded ``unused-public-symbol`` autofix (TASK-82's fix
    protocol, deleted in TASK-124) as the ``"delete-symbol"`` executor of
    :class:`~pypeeker.refactor.delete.DeleteSymbolPlanner`, which re-locates
    the definition by symbol id through the CURRENT index and derives the
    deletion span from its scope. The intent predates that planner (it was
    the data-model expression of a delete-style transform — e.g. the
    deletion half of an inline-then-delete-import chain — so the scheduler
    could order it against renames and reads), which is why it carries only
    a bare ``symbol_id``: the planner resolves the owning file itself.

    Footprint and effect are conservative: a symbol-prefix write on the
    target plus a write of its defining file.

    Remapping covers the epic's rename-vs-delete case: a delete whose target
    a prior rename moved follows the substitution to the new id; a delete
    whose target was already deleted is orphaned.
    """

    symbol_id: str

    kind: ClassVar[str] = "delete-symbol"

    @property
    def anchor(self) -> Anchor:
        """The symbol this delete targets, as a :class:`SymbolAnchor`."""
        return SymbolAnchor(self.symbol_id)

    @property
    def description(self) -> str:
        """One-line summary of the deletion (the name is read off the anchor)."""
        return (
            "delete the unreferenced definition of "
            f"'{strip_shadow(leaf_name(self.symbol_id))}'"
        )

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
class RemoveImportIntent(Intent):
    """Delete the unused import binding ``symbol_id`` (locally bound as ``name``).

    Wraps the superseded ``unused-imports`` autofix (TASK-82's fix protocol,
    deleted in TASK-124) as the ``"remove-import"`` executor of
    :class:`~pypeeker.refactor.imports_ops.RemoveImportPlanner`. ``name`` is
    the locally bound name (the alias, for an aliased import) the planner
    re-verifies against the current bytes at the import symbol's indexed
    location before deleting it — the same defense-in-depth text guard every
    index-anchored fix in the superseded protocol carried.

    Footprint and predicted effect mirror :class:`DeleteSymbolIntent`: a
    symbol-prefix write on the anchor plus a write of its defining file; the
    anchor id is deleted.
    """

    symbol_id: str
    name: str

    kind: ClassVar[str] = "remove-import"

    @property
    def anchor(self) -> Anchor:
        """The import symbol this removal targets, as a :class:`SymbolAnchor`."""
        return SymbolAnchor(self.symbol_id)

    @property
    def description(self) -> str:
        """One-line summary of the deletion."""
        return f"remove the unused import '{self.name}'"

    def footprint(self, store: "IndexStore") -> Footprint:
        """Symbol-prefix write on the anchor plus a write of its defining file."""
        engine = SemanticQueryEngine(store)
        symbol = _resolve_unique(engine, self.symbol_id)
        files = {symbol.location.file_path} if symbol is not None else set()
        return Footprint(
            writes_symbols={self.symbol_id},
            reads_files=files,
            writes_files=files,
        )

    def predicted_effect(self, store: "IndexStore") -> Effect:
        """The import binding disappears; its file is rewritten."""
        return Effect(
            deleted={self.symbol_id},
            files_written=self.footprint(store).writes_files,
        )

    def remap(self, effect: Effect) -> "Intent | OrphanedIntent":
        """Follow renames of the anchor; orphan when it was deleted."""
        return _remap_symbol_anchor(self, effect, describe="remove-import")


@dataclass(frozen=True)
class RewriteStarImportIntent(Intent):
    """Rewrite the ``"*"`` import ``symbol_id`` into an explicit sorted name list.

    Wraps the superseded ``star-imports`` autofix (TASK-82's fix protocol,
    deleted in TASK-124) as the ``"rewrite-star-import"`` executor of
    :class:`~pypeeker.refactor.imports_ops.RewriteStarImportPlanner`.
    ``module`` is the resolved target module the star imports from, carried
    for messages and to detect (at plan time) that the file has grown a
    second star import since detection — see the planner's decline
    conditions, ported verbatim from the fix.

    The rewrite is derived from the *current* project-wide indexes (which
    names the importing module's unresolved references actually need), not
    just the anchor's own file; the footprint stays symbol/file scoped like
    its siblings — a deliberate under-approximation the module-level
    docstring on :mod:`pypeeker.intents.footprint` accepts (project-wide
    read facts are not modeled at this granularity yet).
    """

    symbol_id: str
    module: str

    kind: ClassVar[str] = "rewrite-star-import"

    @property
    def anchor(self) -> Anchor:
        """The ``"*"`` import symbol this rewrite targets, as a :class:`SymbolAnchor`."""
        return SymbolAnchor(self.symbol_id)

    @property
    def description(self) -> str:
        """One-line summary of the rewrite."""
        return (
            f"rewrite the star import from '{self.module}' as an explicit "
            "sorted import list"
        )

    def footprint(self, store: "IndexStore") -> Footprint:
        """Symbol-prefix write on the anchor plus a write of its defining file."""
        engine = SemanticQueryEngine(store)
        symbol = _resolve_unique(engine, self.symbol_id)
        files = {symbol.location.file_path} if symbol is not None else set()
        return Footprint(
            writes_symbols={self.symbol_id},
            reads_files=files,
            writes_files=files,
        )

    def predicted_effect(self, store: "IndexStore") -> Effect:
        """The ``"*"`` binding disappears, replaced by explicit names; file rewritten."""
        return Effect(
            deleted={self.symbol_id},
            files_written=self.footprint(store).writes_files,
        )

    def remap(self, effect: Effect) -> "Intent | OrphanedIntent":
        """Follow renames of the anchor; orphan when it was deleted."""
        return _remap_symbol_anchor(self, effect, describe="rewrite-star-import")


@dataclass(frozen=True)
class TuplifyIntent(Intent):
    """Rewrite the never-mutated list literal bound to ``symbol_id`` as a tuple.

    Wraps the superseded ``prefer-tuple`` autofix (TASK-82's fix protocol,
    deleted in TASK-124) as
    the ``"tuplify"`` executor of
    :class:`~pypeeker.refactor.literals.TuplifyPlanner`. ``name`` is the
    variable name, re-verified against the current bytes at the symbol's
    indexed location, matching every other index-anchored intent's
    defense-in-depth text guard.

    Footprint: a symbol-prefix write on the anchor plus a write of its
    defining file (the literal's brackets are rewritten in place, so the
    binding's id survives — unlike :class:`RemoveImportIntent`, nothing is
    deleted). Predicted effect: the file is rewritten; no id changes.
    """

    symbol_id: str
    name: str

    kind: ClassVar[str] = "tuplify"

    @property
    def anchor(self) -> Anchor:
        """The variable this rewrite targets, as a :class:`SymbolAnchor`."""
        return SymbolAnchor(self.symbol_id)

    @property
    def description(self) -> str:
        """One-line summary of the rewrite."""
        return f"rewrite the list literal bound to '{self.name}' as a tuple"

    def footprint(self, store: "IndexStore") -> Footprint:
        """Symbol-prefix write on the anchor plus a write of its defining file."""
        engine = SemanticQueryEngine(store)
        symbol = _resolve_unique(engine, self.symbol_id)
        files = {symbol.location.file_path} if symbol is not None else set()
        return Footprint(
            writes_symbols={self.symbol_id},
            reads_files=files,
            writes_files=files,
        )

    def predicted_effect(self, store: "IndexStore") -> Effect:
        """File write only — the literal's rewrite does not change the binding's id."""
        return Effect(files_written=self.footprint(store).writes_files)

    def remap(self, effect: Effect) -> "Intent | OrphanedIntent":
        """Follow renames of the anchor; orphan when it was deleted."""
        return _remap_symbol_anchor(self, effect, describe="tuplify")


@dataclass(frozen=True)
class RenameDocstringParamIntent(Intent):
    """Rewrite one stale documented parameter name in ``symbol_id``'s docstring.

    Wraps the superseded ``docstring-drift`` autofix (TASK-82's fix protocol,
    deleted in TASK-124) as the ``"rename-docstring-param"`` executor of
    :class:`~pypeeker.refactor.docstring_ops.DocstringParamRenamePlanner`.
    ``old_param`` is the documented name absent from the signature,
    ``new_param`` the single undocumented signature parameter, and ``style``
    the docstring style detected (or forced by the rule's ``style`` option)
    at detection time — the planner re-parses the CURRENT docstring with it
    and re-derives the drift, so a rename that no longer holds declines
    rather than rewriting the wrong token.

    Anchored on the symbol id rather than on a text position: the docstring's
    byte offset is not recoverable from the index, but the function is, and
    the planner re-locates the docstring inside the symbol's scope span. That
    is what keeps the repair index-anchored — a file that changed since it
    was indexed refuses with ``"stale-index"`` instead of editing bytes the
    finding never saw.

    Footprint: a symbol-prefix write on the anchor plus a write of its
    defining file (the docstring is rewritten in place, so the binding's id
    survives — like :class:`TuplifyIntent`, nothing is deleted). Predicted
    effect: the file is rewritten; no id changes.
    """

    symbol_id: str
    old_param: str
    new_param: str
    style: str

    kind: ClassVar[str] = "rename-docstring-param"

    @property
    def anchor(self) -> Anchor:
        """The FUNCTION/METHOD whose docstring drifted, as a :class:`SymbolAnchor`."""
        return SymbolAnchor(self.symbol_id)

    @property
    def description(self) -> str:
        """One-line summary of the rewrite."""
        return (
            f"rename documented parameter '{self.old_param}' to "
            f"'{self.new_param}' in the docstring of '{self.symbol_id}'"
        )

    def footprint(self, store: "IndexStore") -> Footprint:
        """Symbol-prefix write on the anchor plus a write of its defining file."""
        engine = SemanticQueryEngine(store)
        symbol = _resolve_unique(engine, self.symbol_id)
        files = {symbol.location.file_path} if symbol is not None else set()
        return Footprint(
            writes_symbols={self.symbol_id},
            reads_files=files,
            writes_files=files,
        )

    def predicted_effect(self, store: "IndexStore") -> Effect:
        """File write only — rewriting docstring prose never changes a symbol id."""
        return Effect(files_written=self.footprint(store).writes_files)

    def remap(self, effect: Effect) -> "Intent | OrphanedIntent":
        """Follow renames of the anchor; orphan when it was deleted."""
        return _remap_symbol_anchor(self, effect, describe="rename-docstring-param")


def _elide(text: str, limit: int = 40) -> str:
    """``text`` as a quoted single-line snippet, truncated past ``limit``."""
    flat = text.replace("\n", "\\n")
    return repr(flat if len(flat) <= limit else flat[: limit - 1] + "\u2026")


@dataclass(frozen=True)
class ReplaceTextIntent(Intent):
    """Replace one occurrence of ``old_text`` with ``new_text`` in ``file_path``.

    The text-anchored counterpart to the symbol-anchored intents above:
    wraps the superseded fix protocol's reference ``ReplaceTextFix``
    (TASK-82, deleted in TASK-124) as
    the ``"replace-text"`` executor of
    :class:`~pypeeker.refactor.text_ops.ReplaceTextPlanner`. Like the fix it
    ports it is a **reference** implementation — the minimal shape of a
    text-anchored transform, exercised by the batch/registry tests — and no
    builtin rule attaches it as a remedy: every repair a rule proposes today
    has a symbol to anchor on, including the docstring-param rename (see
    :class:`RenameDocstringParamIntent`, which is index-anchored precisely so
    it can refuse on a stale index rather than re-anchoring by text).
    ``line``/``column`` are the 0-indexed anchor position (see
    :class:`~pypeeker.intents.anchors.RangeAnchor`); the planner re-verifies
    ``old_text`` sits there and, if the file changed, re-anchors to a unique
    occurrence elsewhere before declining.

    Footprint: reads and writes only the anchored file — there is no symbol
    id to declare a prefix write on. Predicted effect: the file is
    rewritten; no id is created, renamed, or deleted (text edits are opaque
    to the symbol-id algebra).

    Remap: the **identity** — unlike every symbol-anchored intent above, a
    text anchor has no substitution to follow through another intent's
    effect. A prior intent that rewrites the same file invalidates this
    anchor's position; that shows up as a footprint conflict (both intents
    write the file) long before ``remap`` would matter, and if the file
    itself is renamed out from under it, the planner's own file-existence
    guard declines at materialization time. Orphaning silently is
    acceptable here (see the module docstring on
    :mod:`pypeeker.intents.anchors`): a stale text anchor is exactly the
    ``TEXT_MISMATCH`` case the planner already declines cleanly.
    """

    file_path: str
    line: int
    column: int
    old_text: str
    new_text: str

    kind: ClassVar[str] = "replace-text"

    @property
    def anchor(self) -> Anchor:
        """This edit's text anchor, as a :class:`RangeAnchor`."""
        return RangeAnchor(self.file_path, self.line, self.column)

    @property
    def description(self) -> str:
        """One-line summary of the replacement.

        Generic by necessity: a text edit carries no semantic subject the
        way a symbol-anchored intent does, so the summary quotes the anchor
        text itself (elided when long) rather than naming a symbol.
        """
        return f"replace {_elide(self.old_text)} with {_elide(self.new_text)}"

    def footprint(self, store: "IndexStore") -> Footprint:
        """Reads and writes the anchored file only (no symbol id to declare)."""
        return Footprint(reads_files={self.file_path}, writes_files={self.file_path})

    def predicted_effect(self, store: "IndexStore") -> Effect:
        """File write only — a text replacement never changes a symbol id."""
        return Effect(files_written={self.file_path})

    def remap(self, effect: Effect) -> "Intent | OrphanedIntent":
        """Identity: a text anchor has no substitution to follow (see the class docstring)."""
        return self


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


def _indexed_modules(store: "IndexStore") -> dict[str, str]:
    """Dotted module path -> indexed file path, for every module in ``store``.

    Keyed on each index's MODULE symbol id, which *is* the dotted module path
    the binder rooted that file's symbol ids at. Ties (two indexes claiming
    one module path — only reachable from a store mid-rename) resolve to the
    lexicographically first path, so the mapping is deterministic.
    """
    modules: dict[str, str] = {}
    for file_path in sorted(store.list_indexed_files()):
        index = store.load(file_path)
        if index is None:
            continue
        for symbol in index.symbols:
            if symbol.kind is SymbolKind.MODULE:
                if symbol.symbol_id:
                    modules.setdefault(symbol.symbol_id, file_path)
                break
    return modules


def _source_root_prefix(module: str, file_path: str) -> str | None:
    """The path prefix that :func:`~pypeeker.paths.module_path_from` stripped.

    Inverts one known (module, path) pair: ``("pkg.mod", "src/pkg/mod.py")``
    yields ``"src/"`` and ``("pkg", "pkg/__init__.py")`` yields ``""``.
    ``None`` when the path does not end in the module's own dotted-to-slash
    form at all (a module path that was not derived from this path — e.g. an
    index bound with an explicit override).
    """
    if not module:
        return None
    normalized = file_path.replace("\\", "/")
    stem = module.replace(".", "/")
    for suffix in (f"{stem}.py", f"{stem}/__init__.py"):
        if normalized == suffix:
            return ""
        if normalized.endswith(f"/{suffix}"):
            return normalized[: -len(suffix)]
    return None


def module_file_path(store: "IndexStore", module: str) -> str | None:
    """The project-relative source path the dotted ``module`` maps to.

    The inverse of :func:`pypeeker.paths.module_path_from`, computed from the
    store alone. ``intents`` may import only ``models``/``query``/``storage``
    (see the module docstring's layering note), so the project's configured
    source roots — ``[tool.pypeeker].src``, read by ``pypeeker.project`` — are
    not reachable from here. They do not need to be: every indexed file
    already carries both halves of the mapping (its path and its MODULE
    symbol's dotted id), so the prefix those two differ by *is* the source
    root, derived from the same store the intent declares its footprint
    against rather than from a config file the simulation cannot see.

    An indexed module answers with its own recorded path. An unindexed one
    answers with the path it *would* occupy under the source root its
    **nearest indexed ancestor package** sits in — ``app.newmod`` lands
    beside ``app``, wherever ``app`` lives — falling back to the prefix the
    project's indexed modules most commonly share (ties broken
    lexicographically) only when no ancestor of the destination is indexed.
    ``None`` only when the store holds no indexed module at all — there is
    then nothing to derive a layout from.

    The ancestor lookup is not a refinement, it is the correctness condition.
    A project with one ``src/``-rooted package and four root-level test
    modules has ``""`` as its *majority* prefix, so a majority-only answer
    puts ``app.newmod`` at ``./app/newmod.py`` while every importer is
    rewritten to ``from app.newmod import ...`` — a module born outside the
    source root, unreachable from the very import it was created for. The
    ancestor's own prefix is already in the store; consulting it first is
    what keeps the destination inside the package it claims to join.

    **Deliberately blind to whether the path exists on disk.** A move's
    footprint declares its destination unconditionally
    (:meth:`MoveSymbolIntent.footprint`), and the batch scheduler's tie-break
    key is derived from footprint file sets
    (``refactor.batch._order_key``); an existence-conditional answer here
    would make the schedule depend on filesystem state. Only the predicted
    :class:`~pypeeker.intents.footprint.Effect` may ask whether the
    destination is a newborn.
    """
    known = _indexed_modules(store)
    recorded = known.get(module)
    if recorded is not None:
        return recorded
    parts = module.split(".")
    for depth in range(len(parts) - 1, 0, -1):
        ancestor = ".".join(parts[:depth])
        ancestor_path = known.get(ancestor)
        if ancestor_path is None:
            continue
        prefix = _source_root_prefix(ancestor, ancestor_path)
        if prefix is not None:
            return prefix + module.replace(".", "/") + ".py"
    counts: dict[str, int] = {}
    for known_module, known_path in known.items():
        prefix = _source_root_prefix(known_module, known_path)
        if prefix is not None:
            counts[prefix] = counts.get(prefix, 0) + 1
    if not counts:
        return None
    prefix = min(counts, key=lambda candidate: (-counts[candidate], candidate))
    return prefix + module.replace(".", "/") + ".py"


@dataclass(frozen=True)
class MoveSymbolIntent(Intent):
    """Move the top-level definition ``symbol_id`` into the module ``dest_module``.

    Wraps :class:`~pypeeker.refactor.move.MoveSymbolPlanner`: the definition
    is deleted from its current module, created in (or appended to)
    ``dest_module``, and every ``from ... import`` that binds it is rewritten
    to the new home. ``dest_module`` is a dotted module path, not a file path
    — where that module lives is :func:`module_file_path`'s question.

    **A move is a rename in id space.** The predicted effect is a single
    ``renamed`` entry, ``{symbol_id: dest_module:leaf}``, which is what makes
    every existing piece of the algebra work on moves with no new
    symbol-remap machinery: :meth:`Effect.remap_id` carries descendants along
    by prefix descent (a moved class takes its methods with it),
    :meth:`Effect.then` composes a move with a later rename, and every
    pending intent anchored on the moved symbol follows it. Only the *file*
    half of the effect needed new fields (TASK-131's
    ``files_created``/``files_deleted``).

    Footprint: writes both the source id and the predicted destination id;
    reads and writes the source file, **the destination file
    unconditionally**, and every importer/reference file — the same superset
    :class:`RenameIntent` builds, plus the destination. The destination is
    declared whether or not it exists on disk, deliberately: the scheduler's
    tie-break reads ``sorted(writes_files | reads_files)[0]``, so a footprint
    that changed shape with the filesystem would make the schedule depend on
    it. Predicted effect: the id rename, the same file writes, and
    ``files_created={destination}`` **only** when the destination does not
    exist — the one existence-conditional half, and the one the batch
    engine's authorization rule
    (:func:`~pypeeker.refactor.batch.flatten_store`) reads.

    Remapping follows the anchor like every other symbol-anchored intent: a
    move whose target a prior rename moved follows the substitution; a move
    whose target was deleted is orphaned.
    """

    symbol_id: str
    dest_module: str

    kind: ClassVar[str] = "move-symbol"

    @property
    def anchor(self) -> Anchor:
        """The definition this move relocates, as a :class:`SymbolAnchor`."""
        return SymbolAnchor(self.symbol_id)

    @property
    def destination_id(self) -> str:
        """The symbol id the moved definition is predicted to carry.

        A prediction, exactly like :func:`replace_leaf_name`'s: the binder
        assigns the real id after the move, and a shadow ordinal could differ
        if the destination already bound the name — which
        ``NoDestinationNameCollision`` refuses at plan time, so in every
        accepted case this is the id the definition actually ends up with.
        """
        return f"{self.dest_module}:{strip_shadow(leaf_name(self.symbol_id))}"

    @property
    def description(self) -> str:
        """One-line summary of the move."""
        return (
            f"move '{strip_shadow(leaf_name(self.symbol_id))}' from "
            f"'{module_of(self.symbol_id)}' to '{self.dest_module}'"
        )

    def destination_path(self, store: "IndexStore") -> str | None:
        """The file path :attr:`dest_module` maps to (see :func:`module_file_path`)."""
        return module_file_path(store, self.dest_module)

    def import_edges(self, store: "IndexStore") -> tuple[EdgeAnchor, ...]:
        """One :class:`~pypeeker.intents.anchors.EdgeAnchor` per import of the target.

        The import half of a move expressed as what it is — a set of *edges*,
        each from an importing module's local IMPORT binding to the
        definition it names. Sorted for determinism; empty when the anchor
        does not resolve uniquely, matching every other declaration on this
        class (materialization-time preconditions are what reject an
        unresolvable anchor, not a raising footprint).

        The planner re-derives its own edge set against the store it plans
        against; this is the intent-level view a caller can inspect before
        submitting, and the shape a refusal names.
        """
        engine = SemanticQueryEngine(store)
        symbol = _resolve_unique(engine, self.symbol_id)
        if symbol is None:
            return ()
        return tuple(
            sorted(
                (
                    EdgeAnchor(importer.symbol_id, symbol.symbol_id)
                    for importer in engine.find_importers(symbol.symbol_id)
                ),
                key=lambda edge: (edge.source_id, edge.target_id, edge.kind),
            )
        )

    def footprint(self, store: "IndexStore") -> Footprint:
        """Symbol writes on both ids; file writes on source, destination and importers."""
        engine = SemanticQueryEngine(store)
        symbol = _resolve_unique(engine, self.symbol_id)
        files: set[str] = set()
        destination = module_file_path(store, self.dest_module)
        if destination is not None:
            files.add(destination)
        if symbol is not None:
            files.add(symbol.location.file_path)
            importers = engine.find_importers(symbol.symbol_id)
            files.update(importer.location.file_path for importer in importers)
            for binding_id in {
                symbol.symbol_id,
                *(importer.symbol_id for importer in importers),
            }:
                files.update(
                    ref.location.file_path
                    for ref in engine.references_to_binding(binding_id)
                )
        return Footprint(
            writes_symbols={self.symbol_id, self.destination_id},
            reads_files=files,
            writes_files=files,
        )

    def predicted_effect(self, store: "IndexStore") -> Effect:
        """The id substitution, the file writes, and a newborn destination if any."""
        destination = module_file_path(store, self.dest_module)
        created: set[str] = set()
        if destination is not None and not store.file_exists(destination):
            created.add(destination)
        return Effect(
            renamed={self.symbol_id: self.destination_id},
            files_written=self.footprint(store).writes_files,
            files_created=created,
        )

    def remap(self, effect: Effect) -> "Intent | OrphanedIntent":
        """Follow renames of the moved symbol; orphan when it was deleted."""
        return _remap_symbol_anchor(self, effect, describe="move-symbol")


__all__ = [
    "OrphanReason",
    "OrphanedIntent",
    "Intent",
    "RenameIntent",
    "InlineVariableIntent",
    "ChangeVisibilityIntent",
    "DeleteSymbolIntent",
    "RemoveImportIntent",
    "RewriteStarImportIntent",
    "TuplifyIntent",
    "RenameDocstringParamIntent",
    "ReplaceTextIntent",
    "ExtractVariableIntent",
    "ExtractMethodIntent",
    "MoveSymbolIntent",
    "module_file_path",
]
