"""Batch scheduler + simulation loop for composite refactor plans (TASK-88).

Two halves, deliberately separated:

* :func:`schedule` is **pure**: given intents and a store, it computes a
  deterministic execution order from explicit ``deps`` plus footprint-conflict
  edges, reports dependency cycles as a structured
  :class:`ScheduleCycleError`, and reports hard conflicts (two id-changing
  intents writing the same symbol — e.g. two renames of one symbol) as
  deterministic drops instead of guessing an order.
* :func:`run_batch` executes the schedule **against a temporary mirror** of
  the project: each intent re-validates its preconditions at its turn by
  re-planning through its planner, its byte edits are spliced bottom-to-top
  per file (the applier's discipline), touched files are re-bound, and the
  intent's :class:`~pypeeker.refactor.footprint.Effect` is folded into a
  running substitution through which every pending intent is remapped
  (orphans drop with machine-readable reasons).

Conflict-edge policy (the scheduler's ordering rules, in precedence order):

1. **Deletes after readers** — when one intent's predicted effect deletes a
   symbol the other's footprint *reads* (``reads_symbols`` or a scoped fact),
   the reader runs first.
2. **Id-changing intents late** — when exactly one side of a conflict
   predicts renames (symbol or file), the non-id-changing intent runs first,
   so position/byte-anchored work lands before names move underneath it.
3. **Deterministic tie-break** — remaining conflicting pairs are ordered by
   ``(primary file, anchor position, intent id)``; the same key drives the
   topological sort, so the whole order is input-order independent.

Simulation substrate (v1): a **temp-dir mirror**, not the in-memory
:class:`~pypeeker.storage.overlay.OverlayIndexStore`. The planners (and
:func:`pypeeker.refactor.dataflow.analyze_range`) read source bytes via
``store.project_root / path`` — straight from disk — which would bypass an
overlay's file-bytes layer entirely. Rather than fork the planners,
:func:`materialize_mirror` copies the indexed files (and ``pyproject.toml``)
into a throwaway directory and the loop runs a plain
:class:`~pypeeker.storage.IndexStore` rooted there: every disk read the
planners perform resolves inside the mirror, writes mutate only the mirror,
and the real working tree stays byte-for-byte untouched. The overlay store
remains the zero-copy future — once the planners read bytes through the
store, ``run_batch`` can swap the mirror for an overlay without changing its
loop (``materialize_mirror`` already reads *through* an overlay when handed
one, so overlay-simulated state feeds the mirror today).

Iteration is a **single pass** over the schedule — no fixpoint loop; TASK-89
(flattening) and TASK-84 own re-running rules over the result. The only
per-intent work is one re-plan, so execution is bounded by the schedule
length times :data:`MAX_PLAN_ATTEMPTS_PER_INTENT`.
"""

from __future__ import annotations

import heapq
import shutil
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from pypeeker.adapters.python_adapter import PythonAdapter
from pypeeker.models.transaction import EditEntry, FileRenameEntry
from pypeeker.project import load_src_roots
from pypeeker.refactor.extract import (
    ExtractMethodError,
    ExtractMethodPlanner,
    ExtractVariableError,
    ExtractVariablePlanner,
)
from pypeeker.refactor.footprint import EMPTY_EFFECT, Effect, Footprint, affects
from pypeeker.refactor.inline import InlineVariableError, InlineVariablePlanner
from pypeeker.refactor.intents import (
    DeleteSymbolIntent,
    ExtractMethodIntent,
    ExtractVariableIntent,
    FixIntent,
    InlineVariableIntent,
    Intent,
    OrphanedIntent,
    RenameIntent,
)
from pypeeker.refactor.planner import RenamePlanError, RenamePlanner
from pypeeker.refactor.simulate import rebind_source
from pypeeker.storage import IndexStore, TransactionStore

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
      intent's anchor (see :class:`~pypeeker.refactor.intents.OrphanedIntent`).
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
    is machine-readable, ``detail`` human-readable.
    """

    intent: Intent
    reason: DropReason
    detail: str = ""


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
class Schedule:
    """A deterministic execution order plus the intents scheduling rejected.

    ``ordered`` holds the intents to execute, in order; ``dropped`` holds
    hard-conflict drops (reason :attr:`DropReason.CONFLICT_DROPPED`),
    including any intents whose explicit dependencies were themselves
    dropped.
    """

    ordered: tuple[Intent, ...]
    dropped: tuple[DroppedIntent, ...] = ()


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
    """

    intent: Intent
    edits: tuple[EditEntry, ...]
    effect: Effect
    file_rename: FileRenameEntry | None = None


@dataclass(frozen=True)
class BatchResult:
    """Outcome of simulating a batch: what ran, what dropped, and the final state.

    ``root`` is the mirror directory holding the final simulated tree and
    ``store`` the (fresh, re-bound) index over it — together the state handle
    TASK-89's flattening consumes: diff ``root`` against the real project to
    produce the composite plan. ``effect`` is the composition of every
    executed intent's effect, i.e. the single substitution mapping submitted
    anchors to their final ids. The caller owns ``root``'s lifetime (see
    :func:`run_batch`).
    """

    executed: tuple[ExecutedIntent, ...]
    dropped: tuple[DroppedIntent, ...]
    root: Path
    store: IndexStore
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


def schedule(intents: list[Intent], store: IndexStore) -> Schedule:
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

    # Hard conflicts first: two id-changing intents writing the same symbol
    # have no resolving order — drop the later-submitted one, then cascade to
    # intents whose explicit deps can no longer execute.
    dropped: list[DroppedIntent] = []
    dropped_ids: set[str] = set()
    for i, a in enumerate(intents):
        if a.intent_id in dropped_ids:
            continue
        for b in intents[i + 1 :]:
            if b.intent_id in dropped_ids:
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
    return Schedule(ordered=tuple(ordered), dropped=tuple(dropped))


# ---------------------------------------------------------------------------
# Mirror substrate
# ---------------------------------------------------------------------------


def materialize_mirror(store: IndexStore, dest: Path) -> IndexStore:
    """Copy the store-visible project state into ``dest``; return a store over it.

    Copies every *indexed* file's bytes (plus ``pyproject.toml``, which
    :func:`~pypeeker.project.load_src_roots` reads for symbol-id module
    paths) and re-saves every :class:`~pypeeker.models.index.FileIndex` into
    a fresh :class:`~pypeeker.storage.IndexStore` rooted at ``dest`` — the v1
    simulation substrate (see the module docstring for the copy-vs-overlay
    trade-off). Bytes are read through ``store.read_file`` when the store
    offers it (an :class:`~pypeeker.storage.overlay.OverlayIndexStore`), so
    overlay-simulated content is honoured; otherwise straight from disk under
    ``store.project_root``. Files indexed but unreadable (overlay-deleted or
    gone from disk) are skipped along with their index entries.
    """
    dest.mkdir(parents=True, exist_ok=True)
    read_file = getattr(store, "read_file", None)
    pyproject = store.project_root / "pyproject.toml"
    if pyproject.is_file():
        shutil.copyfile(pyproject, dest / "pyproject.toml")
    mirror = IndexStore(dest)
    for path in store.list_indexed_files():
        try:
            content = (
                read_file(path)
                if read_file is not None
                else (store.project_root / path).read_bytes()
            )
        except FileNotFoundError:
            continue
        target = dest / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        index = store.load(path)
        if index is not None:
            mirror.save(index)
    return mirror


# ---------------------------------------------------------------------------
# Simulation loop
# ---------------------------------------------------------------------------


class _SpliceMismatch(Exception):
    """An edit's recorded ``old`` text no longer matches the simulated bytes."""


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


@dataclass
class _Materialized:
    """A successful guarded re-plan: the edits to apply at this turn."""

    edits: list[EditEntry] = field(default_factory=list)
    file_rename: FileRenameEntry | None = None


def _load_transaction(
    tx_store: TransactionStore, tx_id: str
) -> _Materialized:
    """The edits a planner just persisted for ``tx_id``, as a materialization."""
    loaded = tx_store.load(tx_id)
    if loaded is None:  # pragma: no cover - planners always persist what they return
        raise RuntimeError(f"planner reported transaction '{tx_id}' but none exists")
    _, edits, file_rename = loaded
    return _Materialized(edits=edits, file_rename=file_rename)


def _materialize(
    intent: Intent, store: IndexStore, tx_store: TransactionStore
) -> _Materialized | str:
    """Re-plan ``intent`` against the current simulated state, or say why not.

    This *is* the guarded re-validation: every planner re-runs its
    precondition set inside ``plan()`` (see
    :mod:`pypeeker.refactor.preconditions`), so constructing the planner over
    the mirror store and planning fresh checks the intent against the world
    as previous intents left it. Returns the materialized edits on success
    and the failure reason (a string) when the intent's guards reject the
    current state.
    """
    try:
        if isinstance(intent, RenameIntent):
            summary = RenamePlanner(store, tx_store).plan(
                intent.symbol_id,
                intent.new_name,
                include_file=intent.include_file,
                include_exports=intent.include_exports,
                include_receivers=intent.include_receivers,
                keep_export=intent.keep_export,
                allow_override_rename=intent.allow_override_rename,
            )
            return _load_transaction(tx_store, summary.tx_id)
        if isinstance(intent, InlineVariableIntent):
            summary = InlineVariablePlanner(store, tx_store).plan(intent.symbol_id)
            return _load_transaction(tx_store, summary.tx_id)
        if isinstance(intent, ExtractVariableIntent):
            summary = ExtractVariablePlanner(store, tx_store).plan(
                intent.file_path, intent.start, intent.end, intent.new_name
            )
            return _load_transaction(tx_store, summary.tx_id)
        if isinstance(intent, ExtractMethodIntent):
            summary = ExtractMethodPlanner(store, tx_store).plan(
                intent.file_path, intent.start_line, intent.end_line, intent.new_name
            )
            return _load_transaction(tx_store, summary.tx_id)
    except (
        RenamePlanError,
        InlineVariableError,
        ExtractVariableError,
        ExtractMethodError,
    ) as error:
        return str(error)
    if isinstance(intent, FixIntent):
        result = intent.fix.plan(store)
        edits = getattr(result, "edits", None)
        if edits is None:
            detail = getattr(result, "reason", "") or "fix declined to plan"
            return f"fix '{intent.fix.fix_id}' declined: {detail}"
        return _Materialized(edits=list(edits))
    if isinstance(intent, DeleteSymbolIntent):
        return (
            "delete-symbol has no planner in v1; the intent is schedulable "
            "(ordering/remap) but not executable"
        )
    return f"no executor for intent kind '{intent.kind}'"


def _apply_to_mirror(
    mirror: IndexStore,
    materialized: _Materialized,
    *,
    adapter: PythonAdapter,
    src_roots: tuple[str, ...],
) -> None:
    """Apply one intent's edits to the mirror and re-bind the touched files.

    Two-phase like the applier: every file's new content is computed (and
    every edit verified) before any file is written, so a
    :class:`_SpliceMismatch` leaves the mirror exactly as it was. A file
    rename moves the mirror file, drops the old index entry, and the new
    path is re-bound under its new module path.
    """
    by_file: dict[str, list[EditEntry]] = {}
    for edit in materialized.edits:
        by_file.setdefault(edit.file, []).append(edit)
    new_contents = {
        path: _splice((mirror.project_root / path).read_bytes(), edits)
        for path, edits in sorted(by_file.items())
    }
    for path, content in new_contents.items():
        (mirror.project_root / path).write_bytes(content)
    touched = sorted(new_contents)
    if materialized.file_rename is not None:
        old_path = materialized.file_rename.old_path
        new_path = materialized.file_rename.new_path
        target = mirror.project_root / new_path
        target.parent.mkdir(parents=True, exist_ok=True)
        (mirror.project_root / old_path).rename(target)
        mirror.remove(old_path)
        touched = [p for p in touched if p != old_path]
        if new_path not in touched:
            touched.append(new_path)
    for path in touched:
        rebind_source(
            mirror,
            path,
            (mirror.project_root / path).read_bytes(),
            adapter=adapter,
            src_roots=src_roots,
        )


def run_batch(
    intents: list[Intent],
    store: IndexStore,
    *,
    policy: BatchPolicy = BatchPolicy.SKIP_AND_REPORT,
    work_dir: Path | None = None,
) -> BatchResult:
    """Schedule ``intents`` and simulate them against a temp mirror of ``store``.

    The real project tree and index are never written: the schedule is
    computed against ``store`` (pure), the project is mirrored into
    ``work_dir`` (created via :func:`tempfile.mkdtemp` when omitted — the
    caller owns the directory's lifetime either way, since
    :attr:`BatchResult.root` is the result's state handle), and execution
    proceeds in schedule order, one guarded pass:

    1. re-validate by re-planning through the intent's planner against the
       mirror (:func:`_materialize`) — failures drop with
       :attr:`DropReason.PRECONDITION_FAILED`;
    2. apply the materialized edits to mirror bytes (bottom-to-top per file)
       and re-bind touched files;
    3. fold the intent's predicted effect into the running substitution and
       remap every pending intent through it — orphans drop with
       :attr:`DropReason.ORPHANED`.

    Under :attr:`BatchPolicy.ALL_OR_NOTHING` any drop — schedule-time or
    execution-time — raises :class:`BatchAborted` with the full drop report.
    Propagates :class:`ScheduleError` / :class:`ScheduleCycleError` from
    scheduling.
    """
    plan = schedule(intents, store)
    dropped: list[DroppedIntent] = list(plan.dropped)
    if policy is BatchPolicy.ALL_OR_NOTHING and dropped:
        raise BatchAborted(tuple(dropped))

    root = (
        Path(tempfile.mkdtemp(prefix="pypeeker-batch-")) if work_dir is None else work_dir
    )
    mirror = materialize_mirror(store, root)
    tx_store = TransactionStore(mirror.project_root)
    adapter = PythonAdapter()
    src_roots = load_src_roots(mirror.project_root)

    executed: list[ExecutedIntent] = []
    dropped_ids = {d.intent.intent_id for d in dropped}
    total_effect = EMPTY_EFFECT
    pending = list(plan.ordered)

    def drop(intent: Intent, reason: DropReason, detail: str) -> None:
        """Record a drop; abort the whole batch under all-or-nothing."""
        dropped.append(DroppedIntent(intent, reason, detail))
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
        outcome = _materialize(intent, mirror, tx_store)
        if isinstance(outcome, str):
            drop(intent, DropReason.PRECONDITION_FAILED, outcome)
            continue
        effect = intent.predicted_effect(mirror)
        try:
            _apply_to_mirror(mirror, outcome, adapter=adapter, src_roots=src_roots)
        except _SpliceMismatch as error:
            drop(intent, DropReason.PRECONDITION_FAILED, str(error))
            continue
        executed.append(
            ExecutedIntent(
                intent=intent,
                edits=tuple(outcome.edits),
                effect=effect,
                file_rename=outcome.file_rename,
            )
        )
        total_effect = total_effect.then(effect)
        still_pending: list[Intent] = []
        for waiting in pending:
            remapped = waiting.remap(effect)
            if isinstance(remapped, OrphanedIntent):
                drop(remapped.intent, DropReason.ORPHANED, remapped.detail)
            else:
                still_pending.append(remapped)
        pending = still_pending

    return BatchResult(
        executed=tuple(executed),
        dropped=tuple(dropped),
        root=root,
        store=mirror,
        effect=total_effect,
        policy=policy,
    )


__all__ = [
    "MAX_PLAN_ATTEMPTS_PER_INTENT",
    "BatchPolicy",
    "DropReason",
    "DroppedIntent",
    "ScheduleError",
    "ScheduleCycleError",
    "BatchAborted",
    "Schedule",
    "ExecutedIntent",
    "BatchResult",
    "schedule",
    "materialize_mirror",
    "run_batch",
]
