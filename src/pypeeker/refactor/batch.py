"""Batch scheduler + simulation loop for composite refactor plans (TASK-88).

Two halves, deliberately separated:

* :func:`schedule` is **pure**: given intents and a store, it computes a
  deterministic execution order from explicit ``deps`` plus footprint-conflict
  edges, reports dependency cycles as a structured
  :class:`ScheduleCycleError`, and reports hard conflicts (two id-changing
  intents writing the same symbol — e.g. two renames of one symbol) as
  deterministic drops instead of guessing an order.
* :func:`run_batch` executes the schedule **against an in-memory overlay**
  of the project: each intent re-validates its preconditions at its turn by
  re-planning through its planner, its byte edits are spliced bottom-to-top
  per file (the applier's discipline), touched files are re-bound, and the
  intent's :class:`~pypeeker.intents.footprint.Effect` is folded into a
  running substitution through which every pending intent is remapped
  (orphans drop with machine-readable reasons).

Conflict-edge policy (the scheduler's ordering rules, in precedence order):

1. **Deletes after readers** — when one intent's predicted effect deletes a
   symbol the other's footprint *reads* (``reads_symbols`` or a scoped fact),
   the reader runs first. The file-lifecycle analogue follows immediately
   and is decided **per path**: a file's reader runs before the intent that
   deletes it, and the intent that *creates* a file runs before anyone
   reading or writing it, so a file born mid-batch is born before it is
   edited whichever order the two intents arrived in — including when both
   intents create a file, as long as only one of them touches the other's
   newborn. Two intents creating the same path have no resolving
   order at all and are a hard conflict, like two renames of one symbol:
   the later-submitted one drops with ``CONFLICT_DROPPED``.
2. **Id-changing intents late** — when exactly one side of a conflict
   predicts renames (symbol or file), the non-id-changing intent runs first,
   so position/byte-anchored work lands before names move underneath it.
3. **Deterministic tie-break** — remaining conflicting pairs are ordered by
   ``(primary file, anchor position, intent id)``; the same key drives the
   topological sort, so the whole order is input-order independent.

Simulation substrate: an in-memory
:class:`~pypeeker.storage.overlay.OverlayIndexStore` layered over the
caller's store — zero copies, nothing on disk. Every planner reads source
bytes and hashes through the store surface (``read_file`` / ``file_exists``
/ ``file_hash``), so a path the batch has not touched reads through to the
real tree while a spliced one serves the simulated bytes; ``write_file``
records the new content, :func:`~pypeeker.refactor.simulate.rebind_source`
re-binds it, and ``overlay.save`` keeps the fresh
:class:`~pypeeker.models.index.FileIndex` in the overlay's own dict. Neither
the working tree, the base store's ``.pypeeker/index/``, nor its in-process
cache is written.

Two consequences the loop is built around. The overlay's ``project_root`` is
the **real** project root (it delegates to the base store), so nothing here
may construct a path from it — that is what made the old temp-dir mirror
safe and is now a live grenade. And because a planner persists the
transaction it plans, :func:`run_batch` takes an explicit ``tx_store``:
callers hand it a scratch :class:`~pypeeker.storage.TransactionStore` under
a temp directory so simulated intermediate transactions never reach the
user's ``.pypeeker/transactions/``. It is never derived from
``store.project_root``.

Iteration is a **single pass over the schedule** — the *batch scheduler* has
no fixpoint loop: every intent is submitted by a caller who already knows
what it wants done, so re-deriving new intents from the simulated result is
not this engine's job. The only per-intent work is one re-plan, so execution
is bounded by the schedule length times
:data:`MAX_PLAN_ATTEMPTS_PER_INTENT`. Where the work to be done *is* derived
from the state (``check --fix``, whose repairs reveal repairs), the fixpoint
lives one layer up in :mod:`pypeeker.app.check_fixes`, which drives its own
persistent overlay and calls this module through :func:`apply_to_overlay` and
:func:`flatten_store` — one pass of this engine per iteration, still one
transaction at the end.
"""

from __future__ import annotations

import hashlib
import heapq
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pypeeker.adapters import PythonAdapter
from pypeeker.intents import (
    EMPTY_EFFECT,
    Effect,
    ExtractMethodIntent,
    ExtractVariableIntent,
    Footprint,
    Intent,
    OrphanedIntent,
    affects,
)
from pypeeker.models import (
    EditEntry,
    EditOp,
    FileCreateEntry,
    FileDeleteEntry,
    FileRenameEntry,
    TransactionHeader,
    TransactionSummary,
)
from pypeeker.project import load_src_roots
from pypeeker.query import SemanticQueryEngine
from pypeeker.refactor.registry import Materialized, get_materializer
from pypeeker.refactor.simulate import rebind_source
from pypeeker.storage import IndexStore, OverlayIndexStore, TransactionStore

# Side-effect imports: each module below registers one or more intent-kind
# materializers with the planner registry (see `refactor/registry.py`,
# `register_planner`) as an import-time effect — mirroring how
# `check/builtin/__init__.py` triggers `@register_rule`. `_materialize` below
# is a pure registry lookup, so these imports (not the names they'd otherwise
# bring in) are what make every built-in intent kind resolvable; batch.py no
# longer references the planner classes or error types directly.
from pypeeker.refactor import edits as _edits  # noqa: F401  (delete-symbol)
from pypeeker.refactor import docstring_ops as _docstring_ops  # noqa: F401  (rename-docstring-param)
from pypeeker.refactor import extract as _extract  # noqa: F401  (extract-variable, extract-method)
from pypeeker.refactor import imports_ops as _imports_ops  # noqa: F401  (remove-import, rewrite-star-import)
from pypeeker.refactor import inline as _inline  # noqa: F401  (inline-variable)
from pypeeker.refactor import literals as _literals  # noqa: F401  (tuplify)
from pypeeker.refactor import move as _move  # noqa: F401  (move-symbol)
from pypeeker.refactor import planner as _planner  # noqa: F401  (rename)
from pypeeker.refactor import text_ops as _text_ops  # noqa: F401  (replace-text)
from pypeeker.refactor import visibility_ops as _visibility_ops  # noqa: F401  (change-visibility)

MAX_PLAN_ATTEMPTS_PER_INTENT = 1
"""Re-plan budget per intent per batch: one guarded attempt, no retries.

The loop is a single pass over the schedule; an intent whose re-plan fails is
dropped (or aborts the batch), never retried. Named so the bound is explicit
and greppable rather than implicit in the control flow.
"""


class BatchPolicy(str, Enum):
    """How a batch reacts when an intent cannot execute.

    * ``SKIP_AND_REPORT`` — drop the intent with a machine-readable reason
      and keep going (the default).
    * ``ALL_OR_NOTHING`` — any drop (schedule-time hard conflict or
      execution-time failure) aborts the whole batch via
      :class:`BatchAborted`, carrying the drop report.
    """

    SKIP_AND_REPORT = "skip-and-report"
    ALL_OR_NOTHING = "all-or-nothing"


class DropReason(str, Enum):
    """Machine-readable reason an intent was not executed.

    * ``PRECONDITION_FAILED`` — guarded re-validation at the intent's turn
      failed: its planner refused to re-plan against the current simulated
      state (or its fix declined / its explicit dependency was dropped).
    * ``ORPHANED`` — a previously executed intent's effect deleted this
      intent's anchor (see :class:`~pypeeker.intents.intents.OrphanedIntent`)
      or the file that anchor lives in (TASK-131), leaving the work with
      nothing to land on either way.
    * ``CONFLICT_DROPPED`` — the scheduler found a hard conflict no ordering
      resolves (two id-changing intents writing the same symbol) and dropped
      the later intent deterministically.
    """

    PRECONDITION_FAILED = "precondition-failed"
    ORPHANED = "orphaned"
    CONFLICT_DROPPED = "conflict-dropped"


@dataclass(frozen=True)
class DroppedIntent:
    """An intent the batch did not execute, with why.

    ``intent`` is the intent as it looked when it was dropped (post-remap for
    execution-time drops, as submitted for schedule-time drops); ``reason``
    is machine-readable, ``detail`` human-readable. ``precondition``
    (TASK-125, additive) names the failing
    :class:`~pypeeker.refactor.preconditions.Precondition` for a
    :attr:`DropReason.PRECONDITION_FAILED` drop whose materializer attached
    one (a :class:`~pypeeker.refactor.registry.MaterializeError`'s
    ``precondition``); it is ``None`` for schedule-time drops (``ORPHANED``,
    ``CONFLICT_DROPPED``) and for kinds that refuse without going through
    :func:`~pypeeker.refactor.preconditions.evaluate_in_order`.

    ``code`` (TASK-129, additive — the same move ``precondition`` made) is the
    refusing materializer's own stable machine-readable refusal class, taken
    from a :class:`~pypeeker.refactor.registry.MaterializeError`'s ``code``
    when it set one: ``change-visibility``'s several classes
    (``"export-target"``, ``"protected-public-api"``, ``"rename-refused"``,
    ...) and the remedy planners' legacy slugs (``"stale-index"``,
    ``"text-mismatch"``, ``"file-missing"``, ``"ambiguous"``). It is ``None``
    for schedule-time drops and for materializers that refuse with a plain
    string, whose callers substitute their own default. This is what lets
    :func:`~pypeeker.app.submit.submit_intent` — a batch of one — raise the
    same :class:`~pypeeker.app.submit.SubmitError` code a direct planner call
    used to produce.
    """

    intent: Intent
    reason: DropReason
    detail: str = ""
    precondition: str | None = None
    code: str | None = None


class ScheduleError(Exception):
    """A batch could not be scheduled (malformed input or unresolvable order)."""


class ScheduleCycleError(ScheduleError):
    """The dependency/conflict graph contains a cycle.

    ``cycle`` lists the intent ids along the cycle in edge order (each id's
    intent must run before the next, and the last must run before the first).
    """

    def __init__(self, cycle: tuple[str, ...]) -> None:
        self.cycle = cycle
        super().__init__(
            "dependency cycle between intents: " + " -> ".join((*cycle, cycle[0]))
        )


class BatchAborted(Exception):
    """An all-or-nothing batch hit a drop and refused to continue.

    ``dropped`` carries every drop known at abort time — the triggering drop
    last — so the report names exactly what could not execute.
    """

    def __init__(self, dropped: tuple[DroppedIntent, ...]) -> None:
        self.dropped = dropped
        names = ", ".join(f"{d.intent.intent_id} ({d.reason.value})" for d in dropped)
        super().__init__(f"batch aborted (all-or-nothing): {names}")


@dataclass(frozen=True)
class _Schedule:
    """A deterministic execution order plus the intents scheduling rejected.

    ``ordered`` holds the intents to execute, in order; ``dropped`` holds
    hard-conflict drops (reason :attr:`DropReason.CONFLICT_DROPPED`),
    including any intents whose explicit dependencies were themselves
    dropped.
    """

    ordered: tuple[Intent, ...]
    dropped: tuple[DroppedIntent, ...] = ()


def _unchanged(value: Any) -> Any:
    """Carry a field across the seam as-is (already immutable, or a scalar)."""
    return value


# The ONE place the carried shape is written (TASK-134). Every field of
# `Materialized` that crosses the batch seam is named here exactly once,
# paired with its freeze (Materialized -> ExecutedIntent) and thaw
# (ExecutedIntent -> Materialized) conversion; `ExecutedIntent.from_materialized`
# and `.to_materialized` build their kwargs from this table and are the only
# two carry construction paths in src. Before this, the shape was hand-copied
# in three unlinked places, and that is exactly how `files_created` was once
# added to both dataclasses but silently dropped on the way back out of
# `submit_intent`. `tests/test_submit_one_path.py::TestCarryOutIsSingleSourced`
# reflects over `dataclasses.fields(Materialized)` and fails if a field is
# missing here, so the same omission cannot recur silently.
#
# The two `sorted(...)` calls are the PRE-EXISTING normalization that made
# `ExecutedIntent` hashable (dict/list -> sorted tuple); they are carried
# forward verbatim, not introduced here.
#
# Boundary, deliberately not covered by this table: `Materialized` has a
# fourth, partial producer in `registry.load_transaction`, which rebuilds one
# from a *persisted* transaction and legitimately cannot supply `summary` or
# `warnings` (a transaction does not store them). A new `Materialized` field
# that a persisted transaction *can* express must be wired there too — this
# table only single-sources the batch-seam carry, not every producer.
_CARRIED_FIELDS: Mapping[str, tuple[Callable[[Any], Any], Callable[[Any], Any]]] = {
    # Materialized field -> (freeze for ExecutedIntent, thaw for Materialized).
    "edits": (tuple, list),
    "file_rename": (_unchanged, _unchanged),
    "summary": (_unchanged, _unchanged),
    "warnings": (tuple, list),
    "files_created": (lambda value: tuple(sorted(value.items())), dict),
    "files_deleted": (lambda value: tuple(sorted(value)), list),
}


@dataclass(frozen=True)
class ExecutedIntent:
    """One executed intent and what it did to the simulated state.

    ``intent`` is the intent *as executed* — after any anchor remapping
    through earlier intents' effects, which is why it can differ from the
    submitted intent with the same id. ``edits`` are the byte edits the
    planner materialized; each :class:`~pypeeker.models.transaction.EditEntry`
    carries the SHA-256 of the simulated file it was planned against
    (``file_hash``), which pins *which* intermediate state the offsets are
    valid for. ``effect`` is the predicted effect that was folded into the
    batch substitution after this intent ran.

    ``summary``/``warnings`` (TASK-129, additive) carry out the two fields the
    simulation loop itself never reads: the planner's **own**
    :class:`~pypeeker.models.transaction.TransactionSummary` for the
    transaction it persisted in ``tx_store`` while re-planning, and the
    warnings it attached (promote/demote's barrel-rewrite notes). They are the
    channel that lets :func:`~pypeeker.app.submit.submit_intent` reach planners
    only through this engine and still return the planner-native summary a
    single-intent CLI command echoes — ``operation`` stays ``"rename"`` /
    ``"extract-variable"`` / ``"promote"`` / ..., never ``"batch"``. Every
    builtin materializer sets ``summary``; ``warnings`` is populated only by
    ``change-visibility`` (promote/demote), and both default empty so a
    materializer that sets neither is carried out unchanged.

    ``files_created``/``files_deleted`` (TASK-131) carry the *other* two
    fields the simulation loop consumes and would otherwise strand here: the
    file-lifecycle channel the materializer declared
    (:attr:`~pypeeker.refactor.registry.Materialized.files_created` /
    ``files_deleted``). They ride out for the same reason ``summary`` does —
    :func:`~pypeeker.app.submit.submit_intent` rebuilds a ``Materialized``
    from this record through :meth:`to_materialized`, and a birth or death
    dropped there would leave the rebuilt object describing a move as "delete
    the definition, rewrite every importer to a module nobody creates".
    ``move-symbol`` is the first kind to populate them; both are tuples (not
    the ``Materialized`` dict/list) so this dataclass stays hashable, and both
    default empty.

    Since TASK-134 the carry is not hand-copied anywhere: ``_CARRIED_FIELDS``
    above names the shape once, and :meth:`from_materialized` /
    :meth:`to_materialized` are the only two construction paths across the
    seam. The field declarations below stay explicit because the frozen
    (hashable) forms genuinely differ in type from ``Materialized``'s mutable
    ones — only the *mapping* between them is single-sourced.
    """

    intent: Intent
    edits: tuple[EditEntry, ...]
    effect: Effect
    file_rename: FileRenameEntry | None = None
    summary: TransactionSummary | None = None
    warnings: tuple[str, ...] = ()
    files_created: tuple[tuple[str, bytes], ...] = ()
    files_deleted: tuple[str, ...] = ()

    @classmethod
    def from_materialized(
        cls, intent: Intent, effect: Effect, materialized: Materialized
    ) -> ExecutedIntent:
        """Record one executed intent, carrying IN every field of ``materialized``.

        The only construction path for the carry-in: the fields and their
        freeze conversions come from ``_CARRIED_FIELDS``, so a field added to
        :class:`~pypeeker.refactor.registry.Materialized` cannot be dropped
        here without failing the carry-out test.
        """
        return cls(
            intent=intent,
            effect=effect,
            **{
                name: freeze(getattr(materialized, name))
                for name, (freeze, _thaw) in _CARRIED_FIELDS.items()
            },
        )

    def to_materialized(self) -> Materialized:
        """Rebuild the planner's ``Materialized`` from this record.

        The inverse of :meth:`from_materialized`, over the same table — what
        :func:`~pypeeker.app.submit.submit_intent` hands back so a batch of
        one returns the planner's own edits, summary, warnings and file
        lifecycle, not a truncated view of them.
        """
        return Materialized(
            **{
                name: thaw(getattr(self, name))
                for name, (_freeze, thaw) in _CARRIED_FIELDS.items()
            }
        )


@dataclass(frozen=True)
class BatchResult:
    """Outcome of simulating a batch: what ran, what dropped, and the final state.

    ``store`` is the simulation overlay holding the final simulated bytes and
    the (fresh, re-bound) indexes over them — the whole state handle
    flattening consumes: :func:`flatten_batch` diffs its written paths against
    the real project to produce the composite plan. It owns no files and no
    directory, so there is nothing for the caller to clean up. ``effect`` is
    the composition of every executed intent's effect, i.e. the single
    substitution mapping submitted anchors to their final ids — and, on the
    file axis, the births and deaths those intents declared, which is
    precisely what authorizes :func:`flatten_batch` to emit file
    create/delete entries.
    """

    executed: tuple[ExecutedIntent, ...]
    dropped: tuple[DroppedIntent, ...]
    store: OverlayIndexStore
    effect: Effect = EMPTY_EFFECT
    policy: BatchPolicy = BatchPolicy.SKIP_AND_REPORT


# ---------------------------------------------------------------------------
# Scheduler (pure)
# ---------------------------------------------------------------------------


def _fact_scopes(footprint: Footprint) -> frozenset[str]:
    """Symbol ids that scope this footprint's ``name:<symbol-id>`` fact reads."""
    scopes: set[str] = set()
    for key in footprint.reads_facts:
        _, sep, scope = key.partition(":")
        if sep:
            scopes.add(scope)
    return frozenset(scopes)


def _reads_deleted(reader: Footprint, deleted: frozenset[str]) -> bool:
    """True when ``reader`` reads (symbols or scoped facts) something ``deleted``."""
    targets = reader.reads_symbols | _fact_scopes(reader)
    return any(
        affects(d, t) or affects(t, d) for d in deleted for t in targets
    )


def _reads_file(reader: Footprint, killer: Effect) -> bool:
    """True when ``reader`` reads a file ``killer``'s effect deletes."""
    return bool(reader.reads_files & killer.files_deleted)


def _touches_created(other: Footprint, maker: Effect) -> bool:
    """True when ``other`` reads or writes a file ``maker``'s effect creates.

    Reads *and* writes, unlike :func:`_reads_file`: an intent editing a
    file another intent brings into existence declares it as written, and
    editing a file that does not exist yet is exactly the refusal
    (:class:`_FileMissing`) this ordering rule prevents.
    """
    return bool((other.reads_files | other.writes_files) & maker.files_created)


def _anchor_files(intent: Intent, store: IndexStore) -> frozenset[str]:
    """The file(s) ``intent``'s anchor lives in, as seen through ``store``.

    The narrow counterpart of :meth:`~pypeeker.intents.Intent.footprint`'s
    file sets: where a footprint over-approximates (a rename declares every
    importer and reference file it may touch), this is the *one* file whose
    disappearance leaves the intent with nowhere to land. That distinction is
    what keeps the file-death orphan rule in :func:`run_batch` from firing on
    a rename that merely happened to list the dead file among its importers.

    Two anchor shapes exist and both are handled by name, the same
    duck-typing :func:`~pypeeker.intents.intents._remap_symbol_anchor` uses:
    a path-literal anchor (``file_path`` — replace-text, extract-variable,
    extract-method) needs no resolution, and a ``symbol_id`` anchor is
    resolved through the semantic index. Resolution degrades to the empty set
    exactly like :func:`~pypeeker.intents.intents._resolve_unique`: an anchor
    that does not resolve uniquely is one the intent's own preconditions will
    reject at its turn, and guessing a file for it would orphan on a guess.

    Because a symbol anchor is resolved *through the index*, this must be
    called before the file in question leaves the simulated tree — deleting
    the file takes the resolution with it. :func:`run_batch` therefore
    snapshots the pending intents' anchors ahead of the apply that kills the
    file, not after.
    """
    file_path = getattr(intent, "file_path", None)
    if isinstance(file_path, str):
        return frozenset({file_path})
    symbol_id = getattr(intent, "symbol_id", None)
    if isinstance(symbol_id, str):
        matches = SemanticQueryEngine(store).find_symbol(symbol_id)
        if len(matches) == 1:
            return frozenset({matches[0].location.file_path})
    return frozenset()


def _is_id_changing(effect: Effect) -> bool:
    """True when an effect moves names around (symbol or file renames)."""
    return bool(effect.renamed or effect.files_renamed)


def _anchor_position(intent: Intent) -> tuple[int, int]:
    """A deterministic in-file position for tie-breaking, ``(-1, -1)`` if none.

    Position-anchored intents expose their anchor (extract-variable's start
    position, extract-method's start line); symbol-anchored intents have no
    stable file position, so they sort before positioned ones in the same
    file — arbitrary but fixed, which is all a tie-break needs.
    """
    if isinstance(intent, ExtractVariableIntent):
        return intent.start
    if isinstance(intent, ExtractMethodIntent):
        return (intent.start_line, 0)
    return (-1, -1)


def _order_key(intent: Intent, footprint: Footprint) -> tuple[str, tuple[int, int], str]:
    """The deterministic ``(file, position, intent id)`` tie-break key."""
    files = sorted(footprint.writes_files | footprint.reads_files)
    return (files[0] if files else "", _anchor_position(intent), intent.intent_id)


def _shared_written_symbol(a: Footprint, b: Footprint) -> str | None:
    """An identical symbol id both footprints declare as written, if any.

    Exact-match on purpose: a rename of ``m:Foo`` and a rename of
    ``m:Foo.method`` overlap prefix-wise but compose through remapping
    (whichever runs first, the other's anchor follows the substitution); only
    two intents claiming the *same* id — two renames of one symbol — have no
    order that preserves both, which is the hard-conflict shape.
    """
    shared = a.writes_symbols & b.writes_symbols
    return min(shared) if shared else None


def _find_cycle(
    nodes: list[str], predecessors: dict[str, set[str]]
) -> tuple[str, ...]:
    """A cycle among ``nodes``, every one of which has a predecessor in ``nodes``.

    Walks predecessors deterministically (smallest id first) from the
    smallest node until one repeats, then returns the loop in forward (edge)
    order.
    """
    remaining = set(nodes)
    walk: list[str] = [min(remaining)]
    seen = {walk[0]}
    while True:
        pred = min(p for p in predecessors[walk[-1]] if p in remaining)
        if pred in seen:
            return tuple(reversed(walk[walk.index(pred) :]))
        walk.append(pred)
        seen.add(pred)


def schedule(intents: list[Intent], store: IndexStore) -> _Schedule:
    """Order ``intents`` deterministically; report cycles and hard conflicts.

    Pure with respect to ``store`` (footprints and predicted effects are
    computed against it, nothing is written). Edges come from each intent's
    explicit ``deps`` plus one edge per conflicting footprint pair, oriented
    by the module-docstring policy; the topological sort breaks ties by
    ``(file, position, intent id)``, so the result depends only on the set of
    intents, never their submission order — except for hard conflicts, where
    the *later-submitted* of the two unorderable intents is dropped
    (deterministic, and the only place submission order matters).

    Raises :class:`ScheduleError` on duplicate or unknown intent ids and
    :class:`ScheduleCycleError` when the dependency/conflict graph cannot be
    ordered; the error lists the offending cycle.
    """
    ids = [intent.intent_id for intent in intents]
    if len(set(ids)) != len(ids):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise ScheduleError(f"duplicate intent ids: {', '.join(dupes)}")
    by_id = {intent.intent_id: intent for intent in intents}
    for intent in intents:
        unknown = sorted(intent.deps - by_id.keys())
        if unknown:
            raise ScheduleError(
                f"intent '{intent.intent_id}' depends on unknown intent(s): "
                + ", ".join(unknown)
            )

    footprints = {i.intent_id: i.footprint(store) for i in intents}
    effects = {i.intent_id: i.predicted_effect(store) for i in intents}

    # Hard conflicts first: two id-changing intents writing the same symbol,
    # or two intents creating the same path, have no resolving order — drop
    # the later-submitted one, then cascade to intents whose explicit deps
    # can no longer execute.
    dropped: list[DroppedIntent] = []
    dropped_ids: set[str] = set()
    for i, a in enumerate(intents):
        if a.intent_id in dropped_ids:
            continue
        for b in intents[i + 1 :]:
            if b.intent_id in dropped_ids:
                continue
            both_create = (
                effects[a.intent_id].files_created & effects[b.intent_id].files_created
            )
            if both_create:
                dropped.append(
                    DroppedIntent(
                        b,
                        DropReason.CONFLICT_DROPPED,
                        f"hard conflict with '{a.intent_id}': both create "
                        f"'{min(both_create)}'; no ordering resolves it",
                    )
                )
                dropped_ids.add(b.intent_id)
                continue
            if not (
                _is_id_changing(effects[a.intent_id])
                and _is_id_changing(effects[b.intent_id])
            ):
                continue
            shared = _shared_written_symbol(
                footprints[a.intent_id], footprints[b.intent_id]
            )
            if shared is not None:
                dropped.append(
                    DroppedIntent(
                        b,
                        DropReason.CONFLICT_DROPPED,
                        f"hard conflict with '{a.intent_id}': both rename "
                        f"'{shared}'; no ordering resolves it",
                    )
                )
                dropped_ids.add(b.intent_id)
    changed = True
    while changed:  # cascade: bounded by len(intents) removals
        changed = False
        for intent in intents:
            if intent.intent_id in dropped_ids:
                continue
            lost = sorted(intent.deps & dropped_ids)
            if lost:
                dropped.append(
                    DroppedIntent(
                        intent,
                        DropReason.CONFLICT_DROPPED,
                        f"explicit dependency '{lost[0]}' was dropped",
                    )
                )
                dropped_ids.add(intent.intent_id)
                changed = True

    kept = [i for i in intents if i.intent_id not in dropped_ids]
    successors: dict[str, set[str]] = {i.intent_id: set() for i in kept}
    predecessors: dict[str, set[str]] = {i.intent_id: set() for i in kept}

    def add_edge(first: str, then: str) -> None:
        """Record that ``first`` must execute before ``then``."""
        if then not in successors[first]:
            successors[first].add(then)
            predecessors[then].add(first)

    for intent in kept:
        for dep in intent.deps:
            if dep in successors:
                add_edge(dep, intent.intent_id)

    # Explicit deps are user intent and outrank conflict policy: a pair the
    # dep graph already orders (directly or transitively) gets no conflict
    # edge, so a dep contradicting a policy rule or tie-break does not
    # manufacture a phantom cycle. Cycles reported below therefore come from
    # the deps themselves or from policy edges among dep-unordered pairs.
    dep_reach: dict[str, set[str]] = {}
    for node in successors:
        seen: set[str] = set()
        stack = list(successors[node])
        while stack:
            current = stack.pop()
            if current not in seen:
                seen.add(current)
                stack.extend(successors[current])
        dep_reach[node] = seen

    for i, a in enumerate(kept):
        fa, ea = footprints[a.intent_id], effects[a.intent_id]
        for b in kept[i + 1 :]:
            fb, eb = footprints[b.intent_id], effects[b.intent_id]
            if (
                b.intent_id in dep_reach[a.intent_id]
                or a.intent_id in dep_reach[b.intent_id]
            ):
                continue
            if fa.conflicts_with(fb) is None:
                continue
            # Rule 1: deletes after intents that read the deleted target.
            if eb.deleted and not ea.deleted and _reads_deleted(fa, eb.deleted):
                add_edge(a.intent_id, b.intent_id)
            elif ea.deleted and not eb.deleted and _reads_deleted(fb, ea.deleted):
                add_edge(b.intent_id, a.intent_id)
            # Rule 1b: the file-lifecycle analogue, decided per *path*. A
            # file's reader runs before its deleter, and its creator before
            # anyone who touches it — so a file born mid-batch is born before
            # it is read, whichever order the two intents were submitted in.
            #
            # The orienting question is whether the touches-what-you-make
            # relation runs one way only, *not* whether one side alone
            # creates or deletes anything. Two intents that each bring a file
            # into existence are perfectly orderable as long as only one of
            # them edits the other's newborn — the shape a batch of two
            # move-symbol intents produces, where one move's importer rewrite
            # lands in the other's new module. Gating on "exactly one side
            # creates" (the symbol rule's shape, where two deletions really
            # are mutually unorderable) silently dropped that edge and left
            # the pair to the tie-break, which schedules the editor first and
            # drops it against a file that does not exist yet. A genuinely
            # mutual pair — each creating a path the other touches — still
            # falls through to the tie-break, which resolves it
            # deterministically in both submission orders.
            elif _reads_file(fa, eb) and not _reads_file(fb, ea):
                add_edge(a.intent_id, b.intent_id)
            elif _reads_file(fb, ea) and not _reads_file(fa, eb):
                add_edge(b.intent_id, a.intent_id)
            elif _touches_created(fb, ea) and not _touches_created(fa, eb):
                add_edge(a.intent_id, b.intent_id)
            elif _touches_created(fa, eb) and not _touches_created(fb, ea):
                add_edge(b.intent_id, a.intent_id)
            # Rule 2: id-changing intents after non-id-changing ones.
            elif _is_id_changing(ea) and not _is_id_changing(eb):
                add_edge(b.intent_id, a.intent_id)
            elif _is_id_changing(eb) and not _is_id_changing(ea):
                add_edge(a.intent_id, b.intent_id)
            # Rule 3: deterministic tie-break.
            elif _order_key(a, fa) <= _order_key(b, fb):
                add_edge(a.intent_id, b.intent_id)
            else:
                add_edge(b.intent_id, a.intent_id)

    # Kahn's algorithm with a heap on the tie-break key: deterministic order.
    indegree = {node: len(preds) for node, preds in predecessors.items()}
    ready = [
        (_order_key(by_id[node], footprints[node]), node)
        for node, deg in indegree.items()
        if deg == 0
    ]
    heapq.heapify(ready)
    ordered: list[Intent] = []
    while ready:
        _, node = heapq.heappop(ready)
        ordered.append(by_id[node])
        for succ in successors[node]:
            indegree[succ] -= 1
            if indegree[succ] == 0:
                heapq.heappush(
                    ready, (_order_key(by_id[succ], footprints[succ]), succ)
                )
    if len(ordered) != len(kept):
        leftover = sorted(node for node, deg in indegree.items() if deg > 0)
        raise ScheduleCycleError(_find_cycle(leftover, predecessors))
    return _Schedule(ordered=tuple(ordered), dropped=tuple(dropped))


# ---------------------------------------------------------------------------
# Simulation loop
# ---------------------------------------------------------------------------


class _ApplyRefused(Exception):
    """The simulated state cannot carry this intent's materialized work.

    Raised only from :func:`_apply_to_overlay`'s verification phase, i.e.
    **before** any overlay mutation, so the overlay is untouched when it
    propagates and :func:`run_batch` can turn it into an ordinary
    :attr:`DropReason.PRECONDITION_FAILED` drop. ``code`` is the drop's
    machine-readable class, carried onto :attr:`DroppedIntent.code` — the
    same channel a refusing
    :class:`~pypeeker.refactor.registry.MaterializeError` uses, which is what
    makes these refusals reportable rather than a traceback out of
    ``run_batch``. It stays ``None`` for :class:`_SpliceMismatch`, whose
    drops predate the field and whose CLI code must not move.
    """

    code: str | None = None


class _SpliceMismatch(_ApplyRefused):
    """An edit's recorded ``old`` text no longer matches the simulated bytes."""


class _FileMissing(_ApplyRefused):
    """An edit or deletion names a path absent from the simulated tree.

    Before TASK-131 this was a ``FileNotFoundError`` escaping ``run_batch``
    (and the ``batch`` CLI's JSON error envelope) as a traceback. It is a
    planner bug either way — file birth is declared, never inferred from an
    edit against nothing — but a bug the batch reports rather than crashes on.
    """

    code = "file-missing"


class _FileAlreadyExists(_ApplyRefused):
    """A declared file creation names a path the simulated tree already has.

    Deterministic by construction: the check runs against the overlay at the
    intent's turn, so the outcome depends on the schedule, not on timing.
    Two intents in one batch creating the same path never get here — the
    scheduler drops the later-submitted one with
    :attr:`DropReason.CONFLICT_DROPPED` — so this is the *real*-tree
    collision (or a birth an earlier intent already performed).
    """

    code = "file-already-exists"


def _splice(content: bytes, edits: list[EditEntry]) -> bytes:
    """Apply one intent's edits to one file's bytes, bottom-to-top.

    The applier's discipline: sorting by start offset descending means each
    splice leaves every earlier offset valid. Each edit's ``old`` text is
    verified against the bytes it replaces — a mismatch raises
    :class:`_SpliceMismatch`, which the loop reports as a precondition
    failure (the plan was made against bytes that no longer exist).
    """
    result = bytearray(content)
    for edit in sorted(edits, key=lambda e: e.start, reverse=True):
        actual = bytes(result[edit.start : edit.end])
        if actual != edit.old.encode("utf-8"):
            raise _SpliceMismatch(
                f"content mismatch in {edit.file} at byte {edit.start}: "
                f"expected {edit.old!r}, found {actual.decode('utf-8', 'replace')!r}"
            )
        result[edit.start : edit.end] = edit.new.encode("utf-8")
    return bytes(result)


def _materialize(
    intent: Intent, store: IndexStore, tx_store: TransactionStore
) -> Materialized | str:
    """Re-plan ``intent`` against the current simulated state, or say why not.

    A pure lookup into the planner registry (:mod:`pypeeker.refactor.registry`)
    followed by one invocation: every built-in intent kind self-registers a
    materializer next to its planner (see the side-effect imports above), so
    adding a new intent kind never means editing this function. A kind with
    no registered materializer — a registry miss — reproduces the historical
    unknown-kind message; every kind this project ships resolves through the
    registry instead, and since TASK-124 every one of them is planner-backed
    (``delete-symbol``, the last kind whose materializer only explained why it
    could not execute, acquired
    :class:`~pypeeker.refactor.delete.DeleteSymbolPlanner`).

    This *is* the guarded re-validation: every planner-backed materializer
    re-runs its planner's precondition set inside ``plan()`` (see
    :mod:`pypeeker.refactor.preconditions`), so constructing the planner over
    the simulation store and planning fresh checks the intent against the
    world as previous intents left it. Returns the materialized edits on
    success and the failure reason (a string) when the intent's guards reject
    the current state.
    """
    materializer = get_materializer(intent.kind)
    if materializer is None:
        return f"no executor for intent kind '{intent.kind}'"
    return materializer(intent, store, tx_store)


def _apply_to_overlay(
    overlay: OverlayIndexStore,
    materialized: Materialized,
    *,
    adapter: PythonAdapter,
    src_roots: tuple[str, ...],
    rebind: bool = True,
) -> None:
    """Apply one intent's edits to the overlay and re-bind the touched files.

    Two-phase like the applier: every precondition is checked and every
    file's new content computed before any ``write_file``, so any
    :class:`_ApplyRefused` leaves the overlay exactly as it was. Phase two
    then mutates in a fixed order — creations, edits, deletions, rename.

    File birth and death (TASK-131) are declared, never inferred. A birth is
    ``materialized.files_created[path] = content`` and refuses with
    :class:`_FileAlreadyExists` if the simulated tree already has that path;
    a death is a path in ``materialized.files_deleted`` and becomes
    ``delete_file`` (tombstone the bytes) plus ``remove`` (drop the index
    entry), leaving the rebind list. Symmetrically, an edit against a path
    the simulated tree does not have is a :class:`_FileMissing` refusal
    rather than a ``FileNotFoundError`` escaping the batch. A file rename is
    expressed with the same overlay primitives — write the new path's bytes,
    tombstone the old path, drop its index entry — and the new path is
    re-bound under its new module path.

    One :class:`~pypeeker.refactor.registry.Materialized` may not both create
    and edit the same path: the edit is verified against the simulated tree,
    where the newborn does not exist yet, so it refuses with
    :class:`_FileMissing`. That is deliberate parity with
    :meth:`~pypeeker.refactor.applier.TransactionApplier._verify_no_path_conflicts`,
    which refuses exactly that pair of entries in one transaction — a
    creation carries its own full content, so there is nothing an edit in the
    same breath could express that the content cannot. Nothing here builds a
    path from
    ``project_root``: that is the *real* project root under an overlay, so a
    stray :meth:`pathlib.Path.rename` or ``write_bytes`` would mutate the
    user's tree.

    ``rebind=False`` performs the byte half only: the splice, its
    verification, and the ``write_file``/``delete_file``/``remove`` overlay
    mutations all still happen — so the overlay's *content* and its mutation
    record (what :func:`flatten_store` diffs) are identical either way — but
    the touched files are not parsed and bound again. It is the caller's
    declaration that nothing will read the overlay's *index* for these paths;
    :func:`run_batch` only ever passes it for the last intent of a batch (see
    its ``rebind_final`` parameter), because every earlier intent's re-bind is
    what the next intent plans against.
    """
    by_file: dict[str, list[EditEntry]] = {}
    for edit in materialized.edits:
        by_file.setdefault(edit.file, []).append(edit)

    # Phase 1: verify everything, compute every byte payload, mutate nothing.
    for path in sorted(materialized.files_created):
        if overlay.file_exists(path):
            raise _FileAlreadyExists(
                f"cannot create '{path}': a file already exists at that path "
                "in the simulated tree"
            )
    for path in sorted(materialized.files_deleted):
        if not overlay.file_exists(path):
            raise _FileMissing(
                f"cannot delete '{path}': no such file in the simulated tree"
            )
    new_contents: dict[str, bytes] = {}
    for path, edits in sorted(by_file.items()):
        try:
            original = overlay.read_file(path)
        except FileNotFoundError:
            raise _FileMissing(
                f"cannot edit '{path}': no such file in the simulated tree "
                "(a file must be created explicitly, not implied by an edit)"
            ) from None
        new_contents[path] = _splice(original, edits)

    # Phase 2: mutate. Creations, then edits, then deletions, then the rename.
    for path, content in sorted(materialized.files_created.items()):
        overlay.write_file(path, content)
    for path, content in new_contents.items():
        overlay.write_file(path, content)
    touched = sorted(set(new_contents) | set(materialized.files_created))
    for path in sorted(materialized.files_deleted):
        overlay.delete_file(path)
        overlay.remove(path)
    if materialized.files_deleted:
        dead = set(materialized.files_deleted)
        touched = [p for p in touched if p not in dead]
    if materialized.file_rename is not None:
        old_path = materialized.file_rename.old_path
        new_path = materialized.file_rename.new_path
        overlay.write_file(new_path, overlay.read_file(old_path))
        overlay.delete_file(old_path)
        overlay.remove(old_path)
        touched = [p for p in touched if p != old_path]
        if new_path not in touched:
            touched.append(new_path)
    if not rebind:
        return
    for path in touched:
        rebind_source(
            overlay,
            path,
            overlay.read_file(path),
            adapter=adapter,
            src_roots=src_roots,
        )


class OverlayApplyError(Exception):
    """A materialized repair could not be spliced into a caller-owned overlay.

    The public face of :class:`_ApplyRefused` for callers that drive a
    simulation overlay themselves instead of handing intents to
    :func:`run_batch` — today :mod:`pypeeker.app.check_fixes`'s fixpoint
    loop, which re-plans through the engine but splices the surviving set
    itself so that one iteration's repairs land as one batch. ``code`` is the
    refusal's machine-readable class (``"file-missing"``,
    ``"file-already-exists"``, or ``None`` for a splice mismatch, whose drops
    predate the field) — the same value :attr:`DroppedIntent.code` carries
    for the same refusal inside a batch.
    """

    def __init__(self, message: str, *, code: str | None = None) -> None:
        """Store the machine-readable refusal class alongside the message."""
        super().__init__(message)
        self.code = code


def apply_to_overlay(
    overlay: OverlayIndexStore,
    materialized: Materialized,
    *,
    adapter: PythonAdapter | None = None,
    src_roots: tuple[str, ...] | None = None,
) -> None:
    """Splice materialized work into ``overlay`` and re-bind the touched files.

    The substrate seam a caller outside this module simulates through: the
    same two-phase apply :func:`run_batch` performs per intent (verify
    everything, then mutate — so a refusal leaves the overlay untouched),
    with the refusal surfaced as a public :class:`OverlayApplyError` instead
    of an internal drop. Passing one
    :class:`~pypeeker.refactor.registry.Materialized` whose ``edits`` are the
    *union* of several repairs applies them as ONE splice batch, which is
    what a caller that has already de-conflicted byte ranges itself wants:
    serializing them instead would make each later repair's ``old`` text be
    verified against bytes an earlier one already moved.

    ``adapter`` and ``src_roots`` default to a fresh
    :class:`~pypeeker.adapters.PythonAdapter` and the project's configured
    source roots, so callers in packages that may not import ``adapters`` or
    ``project`` (``app``) need not build them. The re-bind is unconditional:
    the whole point of a caller-driven overlay is that something reads the
    simulated *index* afterwards.
    """
    adapter = adapter or PythonAdapter()
    if src_roots is None:
        src_roots = load_src_roots(overlay.project_root)
    try:
        _apply_to_overlay(
            overlay, materialized, adapter=adapter, src_roots=src_roots
        )
    except _ApplyRefused as error:
        raise OverlayApplyError(str(error), code=error.code) from error


def run_batch(
    intents: list[Intent],
    store: IndexStore,
    *,
    tx_store: TransactionStore,
    policy: BatchPolicy = BatchPolicy.SKIP_AND_REPORT,
    rebind_final: bool = True,
) -> BatchResult:
    """Schedule ``intents`` and simulate them on an overlay over ``store``.

    The real project tree, its ``.pypeeker/index/``, and the base store's
    in-process cache are never written: the schedule is computed against
    ``store`` (pure), an
    :class:`~pypeeker.storage.overlay.OverlayIndexStore` is layered over it
    as the simulation substrate, and execution proceeds in schedule order,
    one guarded pass:

    1. re-validate by re-planning through the intent's planner against the
       overlay (:func:`_materialize`) — failures drop with
       :attr:`DropReason.PRECONDITION_FAILED`;
    2. apply the materialized edits to overlay bytes (bottom-to-top per file)
       plus any declared file births/deaths, and re-bind touched files —
       a refusal here (stale bytes, a birth onto an occupied path, an edit
       against a path the simulation does not have) drops the intent with
       :attr:`DropReason.PRECONDITION_FAILED` and a machine-readable
       :attr:`DroppedIntent.code`;
    3. fold the intent's predicted effect into the running substitution and
       remap every pending intent through it — orphans drop with
       :attr:`DropReason.ORPHANED`, whether the effect deleted their anchor
       symbol or the file that anchor lives in.

    ``tx_store`` is where the re-planning in step 1 persists each planner's
    own transaction. Those are simulation intermediates — the durable output
    is the caller's flattened transaction — so callers pass a **scratch**
    store under a temp directory. It is a required parameter precisely so it
    is never derived from ``store.project_root``, which under an overlay is
    the user's real project root (and stays the real root however deeply
    overlays nest).

    Under :attr:`BatchPolicy.ALL_OR_NOTHING` any drop — schedule-time or
    execution-time — raises :class:`BatchAborted` with the full drop report.
    Propagates :class:`ScheduleError` / :class:`ScheduleCycleError` from
    scheduling.

    Each per-intent outcome is carried out whole, not just the parts the
    simulation consumes: an executed intent's
    :class:`~pypeeker.refactor.registry.Materialized` contributes its
    ``summary``/``warnings`` to :class:`ExecutedIntent`, and a refusing
    :class:`~pypeeker.refactor.registry.MaterializeError` contributes its
    ``code``/``precondition`` to :class:`DroppedIntent`. That is what makes
    this the *only* execution path: :func:`~pypeeker.app.submit.submit_intent`
    runs a batch of one here and reconstructs exactly the planner-native
    result (or refusal code) a direct planner call would have produced.

    ``rebind_final=False`` is that caller's opt-out from the one piece of
    simulation work a batch's *last* intent does purely for an observer:
    re-parsing and re-binding the files it just spliced. Step 2 exists so the
    *next* intent plans against the previous one's output, so every intent
    with something still pending behind it re-binds regardless; only the last
    one's re-bind is for whoever reads :attr:`BatchResult.store` afterwards.
    ``submit_intent`` reads nothing from it — it returns the planner's own
    ``Materialized`` and drops the overlay — and it is the engine's hottest
    caller (``check --fix`` submits one batch of one *per remedy*), where that
    re-bind is an entire parse + bind of every touched file per fix. Overlay
    *bytes*, the mutation record, the drop vocabulary, and every
    :class:`ExecutedIntent`/:class:`DroppedIntent` field are unaffected: only
    the overlay's index for the last intent's touched files is left unbuilt,
    so :func:`flatten_store` (a pure byte diff) is equally valid either way.
    The default keeps :attr:`BatchResult.store` fully coherent.
    """
    plan = schedule(intents, store)
    dropped: list[DroppedIntent] = list(plan.dropped)
    if policy is BatchPolicy.ALL_OR_NOTHING and dropped:
        raise BatchAborted(tuple(dropped))

    overlay = OverlayIndexStore(store)
    adapter = PythonAdapter()
    src_roots = load_src_roots(store.project_root)

    executed: list[ExecutedIntent] = []
    dropped_ids = {d.intent.intent_id for d in dropped}
    total_effect = EMPTY_EFFECT
    pending = list(plan.ordered)

    def drop(
        intent: Intent,
        reason: DropReason,
        detail: str,
        precondition: str | None = None,
        code: str | None = None,
    ) -> None:
        """Record a drop; abort the whole batch under all-or-nothing."""
        dropped.append(DroppedIntent(intent, reason, detail, precondition, code))
        dropped_ids.add(intent.intent_id)
        if policy is BatchPolicy.ALL_OR_NOTHING:
            raise BatchAborted(tuple(dropped))

    while pending:
        intent = pending.pop(0)
        lost = sorted(intent.deps & dropped_ids)
        if lost:
            drop(
                intent,
                DropReason.PRECONDITION_FAILED,
                f"explicit dependency '{lost[0]}' was dropped",
            )
            continue
        outcome = _materialize(intent, overlay, tx_store)
        if isinstance(outcome, str):
            drop(
                intent,
                DropReason.PRECONDITION_FAILED,
                outcome,
                getattr(outcome, "precondition", None),
                getattr(outcome, "code", None),
            )
            continue
        effect = intent.predicted_effect(overlay)
        # Snapshot the pending intents' anchor files *before* the apply that
        # may remove them: a `symbol_id` anchor is resolved through the
        # index, so once `delete_file` + `remove` have run there is nothing
        # left to resolve and the orphan rule below would see an empty set
        # for every symbol-anchored intent (the majority of them).
        anchors: dict[str, frozenset[str]] = (
            {w.intent_id: _anchor_files(w, overlay) for w in pending}
            if effect.files_deleted
            else {}
        )
        try:
            _apply_to_overlay(
                overlay,
                outcome,
                adapter=adapter,
                src_roots=src_roots,
                # Only the batch's *last* apply may skip the re-bind: anything
                # still pending re-plans against this intent's output.
                rebind=rebind_final or bool(pending),
            )
        except _ApplyRefused as error:
            drop(
                intent,
                DropReason.PRECONDITION_FAILED,
                str(error),
                code=error.code,
            )
            continue
        executed.append(ExecutedIntent.from_materialized(intent, effect, outcome))
        total_effect = total_effect.then(effect)
        still_pending: list[Intent] = []
        for waiting in pending:
            # File death orphans on the *anchor* file, not on the footprint:
            # a symbol anchor survives a file deletion in id space (nothing
            # renamed or deleted the symbol) while the work itself has
            # nowhere to land. The scheduler's Rule 1b already runs readers
            # first, so reaching here means the pair had no conflict edge to
            # order them by — the honest answer is a machine-readable orphan,
            # not an edit against a file that is gone.
            #
            # Keying on the anchor rather than on ``reads_files`` is what
            # makes the rule both reachable and honest. Reachable, because a
            # symbol-anchored intent's footprint is derived by resolving that
            # symbol, and after the deletion it resolves to nothing — an
            # after-the-fact ``footprint(overlay)`` comes back empty and the
            # intent drops with an opaque "symbol not found" instead. Honest,
            # because a footprint over-approximates: a rename whose importer
            # list merely *included* the dead file is not orphaned by its
            # disappearance, it simply has one fewer importer to rewrite. An
            # intent that writes a dead path without being anchored in it may
            # also be the one bringing it back (delete-then-recreate is a
            # legal batch), and if it is not, :class:`_FileMissing` refuses
            # it a moment later.
            if effect.files_deleted:
                dead = sorted(
                    anchors.get(waiting.intent_id, frozenset())
                    & effect.files_deleted
                )
                if dead:
                    drop(
                        waiting,
                        DropReason.ORPHANED,
                        f"file '{dead[0]}' was deleted by intent "
                        f"'{intent.intent_id}'",
                    )
                    continue
            remapped = waiting.remap(effect)
            if isinstance(remapped, OrphanedIntent):
                drop(remapped.intent, DropReason.ORPHANED, remapped.detail)
            else:
                still_pending.append(remapped)
        pending = still_pending

    return BatchResult(
        executed=tuple(executed),
        dropped=tuple(dropped),
        store=overlay,
        effect=total_effect,
        policy=policy,
    )


# ---------------------------------------------------------------------------
# Flattening (TASK-89)
# ---------------------------------------------------------------------------


class FlattenError(Exception):
    """A simulated batch cannot be flattened into one ordinary transaction.

    Raised when the simulated final state is not expressible over the real
    plan-time tree: a file was *renamed* by an executed intent
    (``--include-file`` renames), or one was created in / deleted from the
    simulation that **no executed intent's effect declared**. A transaction
    encodes file renames as a single
    :class:`~pypeeker.models.transaction.FileRenameEntry` per transaction,
    so a renaming batch must still be applied through its individual
    planners; an undeclared birth or death is a planner bug (see
    :func:`flatten_store`'s authorization rule), not a shape the transaction
    format lacks — declared ones flatten into
    :class:`~pypeeker.models.transaction.FileCreateEntry` /
    :class:`~pypeeker.models.transaction.FileDeleteEntry` entries. Also
    raised when a flattened file or changed region does not decode as
    UTF-8: a transaction records content as ``str``, so undecodable bytes
    cannot be expressed in one at all (see :func:`_flatten_text`).
    """


class StalePreimageError(FlattenError):
    """The real tree moved under a simulation while it was running.

    A simulation's content is *derived* from the base store's bytes, and the
    transaction that flattens it is anchored to those same bytes — its
    ``EditEntry.file_hash`` is the pre-image the applier verifies before
    writing. If the file changed on disk in between, that anchor is read
    fresh at flatten time and the applier's pre-flight check therefore
    passes against bytes the simulation never saw, so the splice would
    overwrite whatever landed in the meantime with no refusal at all.

    Raised by :func:`flatten_store` under ``verify_preimages`` when a path in
    the overlay's :meth:`~pypeeker.storage.overlay.OverlayIndexStore.
    base_preimages` record no longer hashes to what the overlay first read.
    A subclass of :class:`FlattenError` so existing ``except FlattenError``
    handlers keep refusing; callers that want to say *why* catch it first.
    """


@dataclass(frozen=True)
class _FlattenedTransaction:
    """One transaction's worth of entries, as flattened from a simulation.

    ``creates``/``deletes`` are lists (a flatten may express any number of
    file births and deaths); ``header``/``edits`` keep their meaning
    exactly.

    **Attribute access only.** TASK-131 added a 2-element
    iteration/indexing shim as ``(header, edits)`` for callers written
    against the pre-existing tuple return; TASK-134 retired it once the last
    of those callers was migrated. Its docstring claimed "the same shape,
    and the same reasoning, as
    :class:`~pypeeker.storage.transaction_store.LoadedTransaction`", and the
    second half of that was wrong. A *loaded* transaction's header comes
    back from disk carrying ``version = 2`` whenever it has file entries,
    which is precisely what
    :meth:`~pypeeker.storage.transaction_store.TransactionStore.save`
    refuses to write back without both lists — so the destructure there was
    lossy but not silent. A *flattened* transaction's header is minted fresh
    below at the default ``version = 1``, so that guard could never fire for
    it: a 2-element destructure followed by ``save(header, edits)`` would
    have persisted a transaction that edits files without creating the ones
    its edits assume, with no refusal anywhere. The shim was the only
    reachable route to that, and retiring it closes the route.
    """

    header: TransactionHeader
    edits: list[EditEntry]
    creates: list[FileCreateEntry] = field(default_factory=list)
    deletes: list[FileDeleteEntry] = field(default_factory=list)


def _trim_common_lines(original: bytes, final: bytes) -> tuple[int, int, bytes]:
    """The minimal line-aligned byte span of ``original`` that ``final`` changes.

    Trims lines common to both ends (a correctness-preserving shrink of the
    whole-file replace: the applier's text guard still sees
    ``old == content[start:end]`` because the trimmed-away bytes are
    identical in both versions). Returns ``(start, end, new_bytes)`` such
    that splicing ``new_bytes`` over ``original[start:end]`` yields ``final``
    exactly. Trimming at line boundaries keeps the cut points valid UTF-8
    boundaries — the newline byte never occurs inside a multi-byte sequence.
    """
    old_lines = original.splitlines(keepends=True)
    new_lines = final.splitlines(keepends=True)
    limit = min(len(old_lines), len(new_lines))
    prefix = 0
    while prefix < limit and old_lines[prefix] == new_lines[prefix]:
        prefix += 1
    suffix = 0
    while (
        suffix < limit - prefix
        and old_lines[-1 - suffix] == new_lines[-1 - suffix]
    ):
        suffix += 1
    start = sum(len(line) for line in old_lines[:prefix])
    end = len(original) - sum(len(line) for line in old_lines[len(old_lines) - suffix :])
    new_end = len(final) - sum(
        len(line) for line in new_lines[len(new_lines) - suffix :]
    )
    return start, end, final[start:new_end]


def _flatten_op(old: str, new: str) -> EditOp:
    """The :class:`EditOp` encoding of a splice replacing ``old`` with ``new``."""
    if not old:
        return EditOp.INSERT
    if not new:
        return EditOp.DELETE
    return EditOp.REPLACE


def _flatten_text(content: bytes, path: str, *, byte_offset: int = 0) -> str:
    """Decode a flattened entry's bytes, or refuse the whole flatten.

    A transaction records file content as ``str`` (``EditEntry.old``/
    ``new``, ``FileCreateEntry.content``, ``FileDeleteEntry.content``) and is
    JSON-serialised, so bytes that do not decode as UTF-8 cannot be
    expressed in one at all. ``byte_offset`` is the region's start in the
    file, so the reported byte is file-absolute for a region as well as for
    a whole file.
    """
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise FlattenError(
            f"file '{path}' is not valid UTF-8 (byte "
            f"{byte_offset + error.start}: {error.reason}); a transaction "
            "records file content as text, so a simulated change to it "
            "cannot be flattened into one"
        ) from error


def _verify_preimages(
    sim_store: OverlayIndexStore, real_store: IndexStore
) -> None:
    """Refuse if the real tree moved under ``sim_store`` since it read it.

    Checks *every* path the overlay observed, not only the ones it wrote:
    the acceptance rule for a simulating command is that the working tree is
    byte-identical from start to the single apply, and a rule that planned
    against a file someone has since edited derived its repairs from bytes
    that are gone. Fails closed on the first mismatch, in sorted path order
    so the message is deterministic. Nothing has been written when this
    raises — the flatten happens before the apply.
    """
    for path, expected in sorted(sim_store.base_preimages().items()):
        actual = real_store.file_hash(path) if real_store.file_exists(path) else None
        if actual == expected:
            continue
        raise StalePreimageError(
            f"file '{path}' changed on disk while the simulation was running; "
            "the simulated result was derived from bytes that are no longer "
            "there, so it cannot be flattened into a transaction anchored to "
            "them"
        )


def flatten_store(
    sim_store: OverlayIndexStore,
    real_store: IndexStore,
    *,
    operation: str,
    authorized_created: frozenset[str] = frozenset(),
    authorized_deleted: frozenset[str] = frozenset(),
    verify_preimages: bool = False,
) -> _FlattenedTransaction:
    """Diff a simulation overlay against the real tree into ONE transaction.

    The substrate-level seam :func:`flatten_batch` delegates to, factored so
    every producer of a simulated tree flattens it the same way — a
    ``run_batch`` result, a fixpoint loop with no ``ExecutedIntent`` list to
    inspect, or a planner that legitimately creates and deletes files.

    The file set comes from the overlay's own mutation record
    (:meth:`~pypeeker.storage.overlay.OverlayIndexStore.overlaid_files` and
    :meth:`~pypeeker.storage.overlay.OverlayIndexStore.deleted_files`), not a
    directory walk: a path the simulation never wrote reads through to the
    real tree and therefore diffs to nothing, so the written paths *are* the
    candidate set. For each of them the *final* simulated content is diffed
    against the *original* real-tree content and emitted as one line-trimmed
    splice (:class:`~pypeeker.models.transaction.EditEntry`) anchored to the
    real plan-time file: ``file_hash`` is the real file's hash and ``old``
    the original text, so the existing
    :class:`~pypeeker.refactor.applier.TransactionApplier` applies the whole
    batch atomically (hash-verified) and ``rollback`` restores the originals
    byte-identically, both unchanged. Intermediate per-intent edits are
    deliberately discarded: they are anchored to simulated intermediate
    states that never exist on disk.

    ``authorized_created`` / ``authorized_deleted`` name the paths a caller
    has *declared* it means to create or delete. They are explicit
    parameters rather than something derived from the simulation, because
    the authority lives with whoever produced it. A declared birth becomes a
    :class:`~pypeeker.models.transaction.FileCreateEntry` carrying the
    simulation's final bytes; a declared death becomes a
    :class:`~pypeeker.models.transaction.FileDeleteEntry` carrying the real
    file's pre-image, so the applier's inverse is exact in both directions.

    Anything *outside* those sets is refused with :class:`FlattenError`, and
    so is a file or changed region whose bytes do not decode as UTF-8 — the
    error names the offending path.
    The overlay does not copy or walk a tree — every path it holds got there
    through an explicit ``write_file`` or ``delete_file`` — so an overlaid
    path with no real counterpart and no declaring intent means a planner
    brought a file into existence that its
    :class:`~pypeeker.intents.footprint.Effect` never claimed, i.e. an
    under-declared effect, not a leaky substrate. Refusing keeps the effect
    algebra load-bearing: scheduling, remapping and flattening all reason
    about the declared sets, and a birth outside them is invisible to every
    one of them. Both default to empty, which is exactly the pre-TASK-131
    contract — no caller creates or deletes files.

    A path deleted *and* re-created inside one simulation needs neither
    authorization: ``write_file`` clears the tombstone, so the path is an
    ordinary overlaid file over an existing real one and flattens to a plain
    edit. Created-then-deleted nets to nothing the same way — the tombstone
    is over a path the real tree never had.

    ``verify_preimages`` closes the window between the simulation's reads
    and this diff. Every edit below is anchored to the real file as read
    *here*, so a file someone changed on disk while the simulation ran is
    invisible: the applier's hash pre-flight compares against the already-
    changed bytes, passes, and the whole-region splice overwrites the change
    without a word. Passing ``True`` re-checks the overlay's
    :meth:`~pypeeker.storage.overlay.OverlayIndexStore.base_preimages`
    record — what the base held when the simulation first read or wrote each
    path — and raises :class:`StalePreimageError` instead. It is opt-in
    because it is only *worth* paying for when the window is wide: a
    ``run_batch`` simulation flattens microseconds after its own reads,
    while the ``check --fix`` fixpoint's window is a whole multi-iteration
    run. Correct only when ``real_store`` is ``sim_store``'s own base — a
    nested overlay's record describes simulated bytes, not the tree.

    The returned header uses ``operation`` with a fresh ``tx_id``; the
    rename-shaped ``symbol_id``/``old_name``/``new_name`` fields are empty,
    the same convention ``check --fix`` established for multi-symbol
    transactions. Persisting via
    :meth:`~pypeeker.storage.TransactionStore.save` is the caller's job. The
    edit list is empty when the simulation was a net no-op.
    """
    if verify_preimages:
        _verify_preimages(sim_store, real_store)

    deletes: list[FileDeleteEntry] = []
    for path in sim_store.deleted_files():
        if not real_store.file_exists(path):
            continue  # nothing in the user's tree to remove
        if path not in authorized_deleted:
            raise FlattenError(
                f"file '{path}' was deleted in the simulated batch; file "
                "deletions cannot be flattened into a single transaction (v1)"
            )
        original = real_store.read_file(path)
        deletes.append(
            FileDeleteEntry(
                path=path,
                content=_flatten_text(original, path),
                file_hash=hashlib.sha256(original).hexdigest(),
            )
        )

    creates: list[FileCreateEntry] = []
    edits: list[EditEntry] = []
    for path in sim_store.overlaid_files():
        if not real_store.file_exists(path):
            if path not in authorized_created:
                raise FlattenError(
                    f"file '{path}' was created in the simulated batch; file "
                    "creations cannot be flattened into a single transaction (v1)"
                )
            content = sim_store.read_file(path)
            creates.append(
                FileCreateEntry(
                    path=path,
                    content=_flatten_text(content, path),
                    content_hash=hashlib.sha256(content).hexdigest(),
                )
            )
            continue
        original = real_store.read_file(path)
        final = sim_store.read_file(path)
        if original == final:
            continue
        start, end, new_bytes = _trim_common_lines(original, final)
        old_text = _flatten_text(original[start:end], path, byte_offset=start)
        new_text = _flatten_text(new_bytes, path, byte_offset=start)
        edits.append(
            EditEntry(
                file=path,
                start=start,
                end=end,
                old=old_text,
                new=new_text,
                file_hash=hashlib.sha256(original).hexdigest(),
                op=_flatten_op(old_text, new_text),
            )
        )

    header = TransactionHeader(
        tx_id=uuid.uuid4().hex[:12],
        symbol_id="",
        old_name="",
        new_name="",
        created_at=datetime.now(timezone.utc).isoformat(),
        operation=operation,
    )
    return _FlattenedTransaction(
        header=header, edits=edits, creates=creates, deletes=deletes
    )


def flatten_batch(
    result: BatchResult, real_store: IndexStore
) -> _FlattenedTransaction:
    """Diff a simulated batch against the real tree into ONE ``"batch"`` transaction.

    The file-rename wall plus a delegation to :func:`flatten_store`: an
    executed intent carrying a
    :class:`~pypeeker.models.transaction.FileRenameEntry` is refused up
    front (v1 transactions hold at most one rename and the applier resolves
    edits against pre-rename paths), and everything else is the seam's
    overlay diff.

    Authorization comes from :attr:`BatchResult.effect` — the composition of
    every *executed* intent's predicted effect — so exactly the file births
    and deaths the batch's intents took responsibility for flatten into
    :class:`~pypeeker.models.transaction.FileCreateEntry` /
    :class:`~pypeeker.models.transaction.FileDeleteEntry` entries, and any
    other one still raises :class:`FlattenError`. Deriving it from the folded
    effect rather than from the materialized work is what makes the effect
    algebra load-bearing rather than advisory: a planner whose edits create a
    file its intent did not declare is refused here, at the last point where
    refusing is free. Note the algebra's cancellations flow straight through
    — a path created and then deleted within the batch is in neither set, and
    it is also in neither of the overlay's, so the two agree. See
    :func:`flatten_store` for the diff's anchoring contract and the returned
    header's shape.
    """
    renamed = [e for e in result.executed if e.file_rename is not None]
    if renamed:
        entry = renamed[0].file_rename
        assert entry is not None  # narrowed by the comprehension
        raise FlattenError(
            f"intent '{renamed[0].intent.intent_id}' renamed "
            f"'{entry.old_path}' to '{entry.new_path}'; file renames cannot "
            "be flattened into a single transaction (v1)"
        )
    return flatten_store(
        result.store,
        real_store,
        operation="batch",
        authorized_created=result.effect.files_created,
        authorized_deleted=result.effect.files_deleted,
    )


__all__ = [
    "MAX_PLAN_ATTEMPTS_PER_INTENT",
    "BatchPolicy",
    "DropReason",
    "DroppedIntent",
    "ScheduleError",
    "ScheduleCycleError",
    "BatchAborted",
    "ExecutedIntent",
    "BatchResult",
    "FlattenError",
    "OverlayApplyError",
    "schedule",
    "run_batch",
    "apply_to_overlay",
    "flatten_batch",
    "flatten_store",
]
