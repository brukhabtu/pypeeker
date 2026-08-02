"""Application service: plan, de-conflict, and apply ``check --fix`` remedies.

Sits above :mod:`pypeeker.check` and :mod:`pypeeker.refactor` — it is the one
place allowed to import both, because ``check`` may not import ``refactor``
(see the ``import-boundaries`` layering). The CLI's ``check --fix`` handler
calls :func:`apply_check_fixes` and does nothing but format the result and
choose an exit code, which is what makes this workflow testable without
spawning the CLI through :class:`click.testing.CliRunner`.

Execution mechanism (TASK-124)
==============================

A rule attaches the repair it proposes as an
:class:`~pypeeker.intents.Intent` on ``Violation.remedy``; this module turns
those intents into edits by submitting each one through the single execution
pipeline, :func:`pypeeker.app.submit.submit_intent` — the intent is scheduled
(a batch of one), its registered planner re-validates every precondition
against the *current* file bytes, and either returns edits or refuses with a
stable code.

Each remedy is submitted **individually**, against whatever store the pass is
planning over, rather than handing the whole set to
:func:`~pypeeker.refactor.batch.run_batch`. That is deliberate, because
``check --fix``'s contract is not the batch engine's:

* the report distinguishes a **conflict** (two repairs whose *byte ranges*
  overlap — one is applied, the other reported under ``skipped_conflicts``)
  from a **refusal** (a planner declined, reported under ``declined``).
  ``run_batch``'s conflict model is footprint-level: every repair in one
  file writes that file, so a batch would serialize them and the later ones
  would either re-plan against an already-mutated overlay or drop as
  precondition failures — the wrong bucket, and a different set;
* every surviving repair in one pass is planned against **one** state, which
  is what makes the ordering key ``(file, first edit offset, fix_id)`` stable
  and the whole run land as ONE ``check-fix`` transaction that a single
  ``rollback <tx_id>`` undoes.

The per-remedy planners persist their own transaction as a side effect of
planning; those go to a **throwaway** transaction store in a temp directory
(``_scratch_transactions``), so the only transaction that reaches the
project is the combined ``check-fix`` one this module writes and applies.

Two paths, one pass (TASK-130)
==============================

:func:`_plan_pass` *is* that pass, and both paths call it:

* **default (``max_iterations=1``)** — one pass over the violations the
  caller was handed, planned against the **real** store, then one
  transaction. Literally the pre-TASK-130 control flow: no overlay, no
  re-run of the rules, no flatten, and no key added to the report. The
  fixpoint machinery is not merely unused on this path, it is unreachable —
  the branch is taken before any of it exists.
* **``--fix-until-clean`` (``max_iterations > 1``)** — repairs reveal
  repairs (deleting a dead private helper can orphan the import that only it
  used), and a repair skipped as a byte conflict deserves a fresh chance
  once the winner has landed. So the loop keeps ONE persistent
  :class:`~pypeeker.storage.overlay.OverlayIndexStore` over the real store
  and, per iteration: re-runs the configured rules **against that simulated
  state**, plans the surviving remedies through
  :func:`~pypeeker.app.submit.submit_intent` (whose own per-call overlay
  nests over the loop's, so a planner reads the previous iteration's bytes),
  splices the kept set into the loop overlay as ONE batch, and re-binds the
  touched files. The real tree is read-only for the whole loop — and is
  re-checked at exit, so an external edit during the run is refused rather
  than overwritten; at exit the overlay's net change is diffed into ONE
  ``check-fix`` transaction
  (:func:`~pypeeker.refactor.batch.flatten_store`) and applied once. Because
  that transaction is the loop's NET diff, a repair the loop applied and a
  later iteration undid reaches nothing: those are reported under
  ``reverted``, never under ``fixes``.

The loop always terminates and always says why — see
:func:`_run_fixpoint` for the four guards and the ``stop_reason``
vocabulary. Rules that *write* through ``context.store.project_root`` are
excluded from the simulated re-runs
(:data:`~pypeeker.check.simulation.SIMULATION_UNSAFE_RULES`); the residual
findings are still computed by the caller's original engine, with the
original config, against the real store after the apply.
"""

from __future__ import annotations

import dataclasses
import hashlib
import tempfile
import uuid
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pypeeker.app.submit import SubmitError, submit_intent
from pypeeker.check import SIMULATION_UNSAFE_RULES, CheckEngine, Violation
from pypeeker.models import Confidence, TransactionHeader
from pypeeker.refactor import (
    ApplyError,
    FlattenError,
    Materialized,
    OverlayApplyError,
    StalePreimageError,
    TransactionApplier,
    apply_to_overlay,
    flatten_store,
)
from pypeeker.storage import IndexStore, OverlayIndexStore, TransactionStore

__all__ = ["CheckFixApplyError", "CheckFixSimulationError", "apply_check_fixes"]

STOP_REASONS: tuple[str, ...] = (
    "quiescent",
    "max-iterations",
    "repeated-fix",
    "cycle",
)
"""Every reason the bounded fixpoint can stop, in no particular order.

``quiescent`` is the only one that means "there was nothing left to repair";
the other three are bounds firing, and each reports ``quiescent: false``.
There is no honest monotonicity argument for this loop — a repair can
strictly increase the finding count — so termination rests on these guards
rather than on a decreasing measure, and ``stop_reason`` is mandatory in the
report so a bound never fires silently.
"""


class CheckFixApplyError(Exception):
    """A planned check-fix transaction failed to apply.

    ``tx_id`` is the transaction that failed (already written to the
    transaction store, so it remains inspectable via ``transactions show``)
    and ``str(error)`` is the underlying :class:`~pypeeker.refactor.applier.
    ApplyError` message.
    """

    def __init__(self, message: str, tx_id: str) -> None:
        """Store the failure message alongside the transaction id."""
        super().__init__(message)
        self.tx_id = tx_id


class CheckFixSimulationError(Exception):
    """The fixpoint loop's simulation could not be carried to a transaction.

    Reachable only under ``--fix-until-clean``, for three failures:

    * ``code="simulation-failed"`` — a splice the overlay refused;
    * ``code="flatten-failed"`` — a simulated state the transaction format
      cannot express (a file born or killed by a remedy, which no rule
      remedy does today);
    * ``code="tree-changed"`` — a file the loop planned against was edited
      on disk while the loop ran, so the simulation is anchored to bytes
      that are gone (:class:`~pypeeker.refactor.batch.StalePreimageError`).
      This is the one that is *not* a bug: it is the fail-closed answer to
      an external write, and re-running the command is the fix.

    All three would otherwise escape as a traceback; carrying a stable
    ``code`` lets the CLI report them through its ordinary flat error
    envelope. Nothing has been written when this is raised: the real tree is
    only touched by the single apply that happens after a successful
    flatten.
    """

    def __init__(self, message: str, *, code: str) -> None:
        """Store the machine-readable failure class alongside the message."""
        super().__init__(message)
        self.code = code


@dataclass
class _CheckFixOutcome:
    """The result of :func:`apply_check_fixes`.

    ``fixes`` are the remedies that made it into the transaction — applied,
    or merely written PENDING under ``plan_only``. On both paths ``fixes``
    is non-empty **iff** ``tx_id`` is set: a run that changed nothing
    reports no repairs. On the default path the relationship is entry-wise
    too (the transaction *is* the kept repairs' edits); under the fixpoint
    the transaction is the net diff of the whole loop, so the guarantee is
    per *file* — see :func:`_run_fixpoint` for what that leaves and what it
    puts in ``reverted``. ``apply_result`` is the
    :class:`~pypeeker.refactor.applier.TransactionApplier` result dict when
    the transaction was applied (``None`` under ``plan_only``, or when there
    was nothing to fix), so callers can report ``files_reindex_failed``
    rather than swallow it.

    ``residual`` is the FULL post-apply violation set (or the original
    ``violations`` when nothing was applied) — callers apply their own
    confidence display filter, matching plain ``check``'s behavior.

    The last five fields describe a **bounded fixpoint run** and are ``None``
    on the default single-pass path, which is what keeps that path's report
    byte-identical: the CLI emits them only when ``stop_reason`` is set.
    ``iterations_run`` is how many passes ran (the last one is the pass that
    decided to stop), ``quiescent`` says whether the loop ran out of work as
    opposed to hitting a bound, and ``stop_reason`` is one of
    :data:`STOP_REASONS`.

    ``reverted`` holds the repairs the loop applied **to the simulation**
    and then threw away: every file they touched ended the run byte-
    identical to the real tree, so nothing about them reaches the
    transaction. It is what keeps ``fixes`` honest without losing the record
    of what the loop did — an oscillation caught by the ``cycle`` guard
    reports ``fixes: []`` and names the pair that undid each other here.

    ``iterations`` is the per-iteration breakdown, in iteration order, and
    its counts are deliberately *not* all the same thing. ``fixes`` and
    ``reverted`` are that iteration's contributions to the two lists above —
    so those two columns sum to ``len(fixes)`` and ``len(reverted)``, and
    ``fixes`` is 0 for a pass whose repairs were abandoned by the
    repeated-fix guard. ``skipped_conflicts``/``declined`` are what that
    pass *planned*, before the cross-iteration dedupe: a conflict loser
    counted in iteration 1 and applied in iteration 2 shows in both columns
    of the breakdown while appearing only under ``fixes`` in the report,
    which is what makes the breakdown a record of the loop's work rather
    than a second copy of its verdicts.
    """

    fixes: list[dict]
    skipped_conflicts: list[dict]
    declined: list[dict]
    residual: list[Violation]
    tx_id: str | None
    apply_result: dict | None = None
    reverted: list[dict] | None = None
    iterations: list[dict] | None = None
    iterations_run: int | None = None
    quiescent: bool | None = None
    stop_reason: str | None = None


def auto_fixable(violation: Violation) -> bool:
    """Whether a finding's remedy may be applied without human review.

    The single auto-fix eligibility policy, shared by ``check --fix`` and
    ``plan-batch``'s ``fix`` intent expansion: the finding must carry a
    remedy and be certain (DECLARED confidence) — heuristic/inferred/unknown
    findings never auto-apply.
    """
    return violation.remedy is not None and violation.confidence is Confidence.DECLARED


@contextmanager
def _scratch_transactions() -> Iterator[TransactionStore]:
    """A throwaway :class:`TransactionStore` under a temp directory.

    Every planner persists the transaction it plans; ``check --fix`` wants
    only the ONE combined ``check-fix`` transaction on disk (see the module
    docstring), so the per-remedy ones are written here and discarded.
    """
    with tempfile.TemporaryDirectory(prefix="pypeeker-check-fix-") as tmp:
        yield TransactionStore(Path(tmp))


def _order(item: tuple[Violation, Materialized]) -> tuple[str, int, str]:
    """Deterministic application order: earliest edit, then fix_id."""
    violation, materialized = item
    first = min((edit.file, edit.start) for edit in materialized.edits)
    return (first[0], first[1], violation.remedy.intent_id)


def _plan_pass(
    store: IndexStore | OverlayIndexStore, violations: list[Violation]
) -> tuple[list[dict], list[dict], list[dict], list[Materialized]]:
    """One ``check --fix`` pass over ``violations``, planned against ``store``.

    Returns ``(fixes, skipped_conflicts, declined, kept)``: the report
    entries for the repairs that survived, for the ones whose byte ranges
    overlapped an already-kept repair, and for the ones whose planner
    refused — plus the surviving
    :class:`~pypeeker.refactor.registry.Materialized` objects, in the same
    order as ``fixes``.

    ``store`` is the real :class:`~pypeeker.storage.IndexStore` on the
    default path and the fixpoint loop's simulation overlay under
    ``--fix-until-clean``; nothing here knows the difference, which is
    exactly the point — the two paths share one definition of what a pass
    *is*, so the flagged report cannot drift into a different set of buckets
    from the frozen one.
    """
    declined: list[dict] = []
    planned: list[tuple[Violation, Materialized]] = []
    with _scratch_transactions() as scratch:
        for violation in violations:
            if not auto_fixable(violation):
                continue
            remedy = violation.remedy
            try:
                materialized = submit_intent(remedy, store, scratch)
            except SubmitError as error:
                declined.append(
                    {
                        "fix_id": remedy.intent_id,
                        "reason": error.code,
                        "detail": error.detail,
                    }
                )
            else:
                planned.append((violation, materialized))

    planned.sort(key=_order)
    fixes: list[dict] = []
    skipped_conflicts: list[dict] = []
    kept: list[Materialized] = []
    claimed: dict[str, list[tuple[int, int]]] = {}
    for violation, materialized in planned:
        entry = {
            "fix_id": violation.remedy.intent_id,
            "description": violation.remedy.description,
            "violation": str(violation),
        }
        conflicts = any(
            edit.start < end and start < edit.end
            for edit in materialized.edits
            for start, end in claimed.get(edit.file, ())
        )
        if conflicts:
            skipped_conflicts.append(entry)
            continue
        kept.append(materialized)
        fixes.append(entry)
        for edit in materialized.edits:
            claimed.setdefault(edit.file, []).append((edit.start, edit.end))
    return fixes, skipped_conflicts, declined, kept


def apply_check_fixes(
    store: IndexStore,
    transaction_store: TransactionStore,
    engine: CheckEngine,
    violations: list[Violation],
    *,
    plan_only: bool = False,
    max_iterations: int = 1,
) -> _CheckFixOutcome:
    """Plan, de-conflict, and apply violation-attached remedies (``check --fix``).

    * Only remedies on certain (DECLARED-confidence) findings are planned;
      heuristic/inferred/unknown findings never auto-apply.
    * Each remedy is submitted through :func:`~pypeeker.app.submit.submit_intent`
      against the real store; a planner refusal becomes a ``declined`` entry
      carrying the planner's stable code (``"ambiguous"``, ``"stale-index"``,
      ``"text-mismatch"``, ``"file-missing"``), or the generic
      ``"plan-refused"`` when the refusing precondition carries no legacy
      slug (the remove-import and delete-symbol UTF-8 guards).
    * Planned remedies are considered in deterministic (file, start, fix_id)
      order; one whose byte ranges overlap an already-kept repair in the same
      file is skipped as a conflict — one repair per file region, across rules.
    * The surviving edits are written as ONE ``check-fix`` transaction and
      applied immediately through :class:`~pypeeker.refactor.applier.
      TransactionApplier`, so the standard lifecycle holds: hashes are
      verified before writing, edited files are re-indexed, and the APPLIED
      transaction stays on disk for ``rollback <tx_id>`` /
      ``transactions show <tx_id>``.
    * ``engine`` is re-run after the apply to compute the residual count.

    With ``plan_only`` (the CLI's ``check --fix --plan``) everything above
    happens except the apply: the same ONE transaction is written PENDING
    for ``transactions show <tx_id>`` / a later ``apply <tx_id>``, no file is
    touched, and ``residual`` is the unmodified input set. This is the
    ``--plan`` half of the uniform mutation grammar — the only way to
    preview autofixes before they hit disk.

    ``max_iterations`` above 1 (the CLI's ``--fix-until-clean``) switches to
    the bounded fixpoint in :func:`_run_fixpoint`: the same pass, repeated
    against simulated post-fix state until quiescence or a bound, still ONE
    transaction. At the default of 1 that machinery is not merely unused but
    unreachable — the branch below returns before any overlay exists — so
    the default report is byte-identical by construction rather than by
    promise.

    Raises :class:`CheckFixApplyError` when the apply itself fails (the
    transaction was still written and stays inspectable), and
    :class:`CheckFixSimulationError` (fixpoint mode only) when the
    simulation cannot be carried to a transaction at all.
    """
    if max_iterations > 1:
        return _run_fixpoint(
            store,
            transaction_store,
            engine,
            violations,
            plan_only=plan_only,
            max_iterations=max_iterations,
        )

    fixes, skipped_conflicts, declined, kept = _plan_pass(store, violations)

    tx_id: str | None = None
    apply_result: dict | None = None
    residual = violations
    if kept:
        tx_id = uuid.uuid4().hex[:12]
        header = TransactionHeader(
            tx_id=tx_id,
            symbol_id="",
            old_name="",
            new_name="",
            created_at=datetime.now(timezone.utc).isoformat(),
            operation="check-fix",
        )
        transaction_store.save(
            header, [edit for materialized in kept for edit in materialized.edits]
        )
        if not plan_only:
            try:
                apply_result = TransactionApplier(store, transaction_store).apply(tx_id)
            except ApplyError as e:
                raise CheckFixApplyError(str(e), tx_id) from e
            residual = engine.run()  # the applier re-indexed the edited files

    return _CheckFixOutcome(
        fixes=fixes,
        skipped_conflicts=skipped_conflicts,
        declined=declined,
        residual=residual,
        tx_id=tx_id,
        apply_result=apply_result,
    )


def _simulated_state_hash(sim_store: OverlayIndexStore) -> str:
    """A digest of the whole simulated tree: sorted ``(path, sha256(bytes))``.

    Covers *every* indexed path, not just the ones an iteration touched, so
    an oscillation that shuffles work between files is still recognized as a
    repeat. Paths the simulation deleted hash as a tombstone rather than
    vanishing, so "deleted" and "never existed" are distinguishable states.
    The real tree is read-only for the whole loop, so an untouched path
    contributes the same bytes on every iteration.
    """
    digest = hashlib.sha256()
    for path in sorted(sim_store.list_indexed_files()):
        digest.update(path.encode("utf-8"))
        try:
            content = sim_store.read_file(path)
        except FileNotFoundError:
            digest.update(b"\0<deleted>\n")
            continue
        digest.update(b"\0" + hashlib.sha256(content).hexdigest().encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _touched_paths(materialized: Materialized) -> frozenset[str]:
    """Every project-relative path one repair's materialized work writes to.

    Edited, created, deleted, or renamed — the union is what decides whether
    the repair's effect survives to the end of a fixpoint run (see
    :func:`_run_fixpoint`). Remedy planners only ever edit today; the other
    three are covered so a future file-lifecycle remedy is classified rather
    than silently counted as landed.
    """
    paths = {edit.file for edit in materialized.edits}
    paths |= set(materialized.files_created)
    paths |= set(materialized.files_deleted)
    if materialized.file_rename is not None:
        paths |= {
            materialized.file_rename.old_path,
            materialized.file_rename.new_path,
        }
    return frozenset(paths)


def _record_verdicts(
    bucket: dict[str, dict], entries: list[dict], applied: set[str]
) -> None:
    """Fold one iteration's non-applied verdicts into a fix_id-keyed bucket.

    ``skipped_conflicts`` and ``declined`` are deduped across iterations by
    ``fix_id``, keeping the LAST verdict: an N-iteration run must not emit N
    copies of the same decline. A fix_id that has already been *applied* is
    dropped entirely — a repair that landed in iteration 1 and is re-proposed
    (and skipped, or refused) against the post-fix state in iteration 2 is
    not news, it is the same repair seen twice, and reporting it in two
    buckets at once would make the report self-contradictory.
    """
    for entry in entries:
        if entry["fix_id"] in applied:
            continue
        bucket[entry["fix_id"]] = entry


def _run_fixpoint(
    store: IndexStore,
    transaction_store: TransactionStore,
    engine: CheckEngine,
    violations: list[Violation],
    *,
    plan_only: bool,
    max_iterations: int,
) -> _CheckFixOutcome:
    """The bounded fixpoint behind ``check --fix --fix-until-clean``.

    N repetitions of :func:`_plan_pass`, each against the state the previous
    one produced, on ONE persistent
    :class:`~pypeeker.storage.overlay.OverlayIndexStore` layered over
    ``store``. Per iteration:

    1. re-run the configured rules against the simulation
       (:class:`~pypeeker.check.CheckEngine` over the overlay), minus
       :data:`~pypeeker.check.simulation.SIMULATION_UNSAFE_RULES` — the
       write-safety guard, since an overlay's ``project_root`` is the
       user's *real* project root;
    2. plan the auto-fixable remedies through :func:`_plan_pass`. Each
       submission builds its own throwaway overlay over this one, so a
       planner's byte reads and hashes see the previous iterations' splices,
       not pre-loop disk;
    3. splice the surviving set into the overlay as ONE batch (never one
       repair at a time: serializing them would make a conflict loser
       re-plan against post-winner bytes and refuse ``text-mismatch`` — the
       wrong bucket) and re-bind the touched files, which is what makes the
       *next* iteration's preconditions (``AnchorIndexFresh`` and friends)
       evaluate against fresh offsets rather than fail closed.

    Four guards bound it, and every one of them names itself in
    ``stop_reason`` (see :data:`STOP_REASONS`):

    * **quiescent** — an iteration kept zero repairs. The only success-shaped
      exit; ``quiescent`` is ``True``.
    * **repeated-fix** — a fix_id already applied in this run is proposed
      again, i.e. the repair did not stick. Checked *before* applying, so the
      re-proposal is abandoned rather than re-applied.
    * **cycle** — the simulated tree hashes to a state already seen, i.e. an
      A→B→A oscillation. This is the guard that actually bounds the loop in
      the general case.
    * **max-iterations** — the caller's cap, the backstop.

    Determinism: each iteration is a pure function of the previous simulated
    tree (rule output is sorted, :func:`_plan_pass` sorts by
    ``(file, start, fix_id)``, splices are order-independent because the kept
    set is non-overlapping), so ``fixes`` comes out in
    ``(iteration, file, start, fix_id)`` order and two runs over identical
    input produce identical reports modulo ``tx_id``.

    **What ``fixes`` means here.** The transaction is the loop's NET diff,
    not a replay of its passes, so "the loop applied this repair" and "this
    repair is in the transaction" are not the same statement and the report
    must not pretend they are. A repair is reported under ``fixes`` only
    when at least one file it touched still differs from the real tree at
    exit; the rest go to ``reverted``. That makes the report's headline
    invariant exact in both directions — ``fixes`` is non-empty **iff**
    ``tx_id`` is set — because every path in the flattened diff was written
    by some kept repair, and a repair whose every path nets to zero
    contributes nothing to write. It is deliberately a *per-file* guarantee,
    not a per-repair one: within a file the diff is one line-trimmed splice,
    so when a later iteration rewrites bytes an earlier repair produced (the
    conflict loser whose line a subsequent repair deletes outright is the
    ordinary case) only their combined result is expressible, and attributing
    it to one of them would be a fiction of a different kind. ``reverted``
    is the honest name for the part that is decidable.

    The real tree is byte-identical from entry until the single apply at
    exit — and that is *checked*, not assumed: the flatten re-verifies the
    overlay's pre-image record (``verify_preimages``), so a file edited on
    disk mid-loop is refused with ``tree-changed`` rather than silently
    overwritten by a whole-region splice whose anchor was read after the
    edit. The overlay's net change against the real store is then flattened
    into ONE ``check-fix`` transaction, which is written (PENDING under
    ``plan_only``) and applied once. ``residual`` comes from the caller's
    ORIGINAL engine — original config, real store — so a rule excluded from
    the simulated re-runs still reports on the final tree.
    """
    sim_store = OverlayIndexStore(store)
    loop_config = dataclasses.replace(
        engine.config,
        rules=tuple(
            rule for rule in engine.config.rules if rule not in SIMULATION_UNSAFE_RULES
        ),
    )

    applied: list[tuple[dict, frozenset[str]]] = []
    skipped_by_id: dict[str, dict] = {}
    declined_by_id: dict[str, dict] = {}
    passes: list[tuple[int, int, int]] = []
    applied_ids: set[str] = set()
    seen_states: set[str] = {_simulated_state_hash(sim_store)}
    iteration = 0
    stop_reason = "quiescent"

    while True:
        iteration += 1
        pass_fixes, pass_skipped, pass_declined, kept = _plan_pass(
            sim_store, CheckEngine(sim_store, loop_config).run()
        )
        repeated = [e for e in pass_fixes if e["fix_id"] in applied_ids]
        if not kept:
            stop_reason = "quiescent"
        elif repeated:
            # The repair did not stick; re-applying it would loop forever.
            stop_reason = "repeated-fix"
        else:
            try:
                apply_to_overlay(
                    sim_store,
                    Materialized(
                        edits=[
                            edit
                            for materialized in kept
                            for edit in materialized.edits
                        ]
                    ),
                )
            except OverlayApplyError as error:
                raise CheckFixSimulationError(
                    f"iteration {iteration}: a planned repair could not be "
                    f"applied to the simulated tree: {error}",
                    code="simulation-failed",
                ) from error
            for entry, materialized in zip(pass_fixes, kept, strict=True):
                entry["iteration"] = iteration
                applied.append((entry, _touched_paths(materialized)))
                applied_ids.add(entry["fix_id"])
                skipped_by_id.pop(entry["fix_id"], None)
                declined_by_id.pop(entry["fix_id"], None)

        _record_verdicts(skipped_by_id, pass_skipped, applied_ids)
        _record_verdicts(declined_by_id, pass_declined, applied_ids)
        passes.append((iteration, len(pass_skipped), len(pass_declined)))
        if not kept or repeated:
            break
        state = _simulated_state_hash(sim_store)
        if state in seen_states:
            stop_reason = "cycle"
            break
        seen_states.add(state)
        if iteration >= max_iterations:
            stop_reason = "max-iterations"
            break

    try:
        flattened = flatten_store(
            sim_store, store, operation="check-fix", verify_preimages=True
        )
    except StalePreimageError as error:
        raise CheckFixSimulationError(str(error), code="tree-changed") from error
    except FlattenError as error:
        raise CheckFixSimulationError(str(error), code="flatten-failed") from error

    changed = (
        {edit.file for edit in flattened.edits}
        | {create.path for create in flattened.creates}
        | {delete.path for delete in flattened.deletes}
    )
    fixes = [entry for entry, paths in applied if paths & changed]
    reverted = [entry for entry, paths in applied if not paths & changed]
    landed_per_pass = Counter(entry["iteration"] for entry in fixes)
    reverted_per_pass = Counter(entry["iteration"] for entry in reverted)
    breakdown = [
        {
            "iteration": n,
            "fixes": landed_per_pass[n],
            "reverted": reverted_per_pass[n],
            "skipped_conflicts": skipped,
            "declined": refused,
        }
        for n, skipped, refused in passes
    ]

    tx_id: str | None = None
    apply_result: dict | None = None
    residual = violations
    if flattened.edits or flattened.creates or flattened.deletes:
        tx_id = flattened.header.tx_id
        transaction_store.save(
            flattened.header,
            flattened.edits,
            creates=flattened.creates,
            deletes=flattened.deletes,
        )
        if not plan_only:
            try:
                apply_result = TransactionApplier(store, transaction_store).apply(tx_id)
            except ApplyError as e:
                raise CheckFixApplyError(str(e), tx_id) from e
            residual = engine.run()  # the applier re-indexed the edited files

    return _CheckFixOutcome(
        fixes=fixes,
        skipped_conflicts=list(skipped_by_id.values()),
        declined=list(declined_by_id.values()),
        residual=residual,
        tx_id=tx_id,
        apply_result=apply_result,
        reverted=reverted,
        iterations=breakdown,
        iterations_run=iteration,
        quiescent=stop_reason == "quiescent",
        stop_reason=stop_reason,
    )
