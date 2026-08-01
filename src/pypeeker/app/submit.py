"""Application service: the single execution pipeline for mutating CLI commands.

TASK-123 ("everything is a batch"): every mutating CLI command builds one or
more :class:`~pypeeker.intents.Intent` objects and submits them here instead
of constructing a planner and calling ``plan()`` directly — a lone refactor
is a batch of one (see architecture.md's target-architecture section, item
3). TASK-129 made that literally true: there is exactly ONE execution path
through this module. Both entry points reach planners only through
:func:`~pypeeker.refactor.batch.run_batch`, which schedules, layers an
in-memory :class:`~pypeeker.storage.overlay.OverlayIndexStore` over the
caller's store, and re-plans each intent through its registered materializer
(:mod:`pypeeker.refactor.registry`). Nothing here looks up a materializer or
touches a planner itself.

A batch of one still returns the *planner's own* result, because
``run_batch`` is handed the caller's real transaction store: the planner
persists its own transaction exactly as a direct ``plan()`` call would, and
:class:`~pypeeker.refactor.batch.ExecutedIntent` carries that planner-native
:class:`~pypeeker.models.transaction.TransactionSummary` (and any
``warnings``) back out. A CLI command echoing ``materialized.summary``
therefore emits byte-identical JSON to the pre-TASK-123 direct planner call —
``operation`` is ``"rename"``/``"promote"``/..., never ``"batch"``. Refusals
come back the same way: :class:`~pypeeker.refactor.batch.DroppedIntent`
carries the materializer's own ``code``/``precondition``, so every refusal
class the direct call could produce still surfaces on :class:`SubmitError`.

:func:`submit_intents` is the plural entry point the design calls for. It
adds no execution path — only the caller-facing contract discrimination the
design fixes: a genuine batch (two or more) gets the batch engine's own
shape (a :class:`~pypeeker.refactor.batch.BatchResult`, the caller's
:class:`~pypeeker.refactor.batch.BatchPolicy`, and a **scratch** transaction
store, since its durable output is the flattened transaction the caller
writes), while a lone intent gets :func:`submit_intent`'s single-submission
contract (a :class:`~pypeeker.refactor.registry.Materialized`, all-or-nothing
semantics, and the caller's own transaction store). No builtin CLI command
submits more than one intent yet (that remains ``plan-batch``'s own job), but
the dispatch lives here so the pipeline has one entry point regardless of
count.

Layering: this module lives in ``app`` (not ``refactor``) by the same
convention every other CLI-facing composition here follows (see
``check_fixes.py``, ``privatize.py``), not because it needs both ``check``
and ``refactor`` — it only needs ``refactor``/``intents``/``storage``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from pypeeker.intents import Intent
from pypeeker.refactor import (
    BatchAborted,
    BatchPolicy,
    BatchResult,
    Materialized,
    ScheduleError,
    run_batch,
)
from pypeeker.storage import IndexStore, TransactionStore

__all__ = ["SubmitError", "submit_intent", "submit_intents"]


class SubmitError(Exception):
    """A submitted intent could not be materialized.

    ``code`` is the stable machine-readable error code the CLI should emit —
    the exact code the pre-TASK-123 direct-planner call used for this
    refusal (e.g. ``"plan-refused"`` for rename/inline/extract-*, or a
    :class:`~pypeeker.refactor.visibility_ops.VisibilityOpError`'s own code
    for promote/demote); ``detail`` is the human-readable message, i.e.
    ``str(error)`` from the underlying failure. ``precondition``
    (TASK-125, additive) names the failing
    :class:`~pypeeker.refactor.preconditions.Precondition` when the
    materializer's :class:`~pypeeker.refactor.registry.MaterializeError`
    attached one; ``None`` for a schedule failure or a materializer that
    refuses without going through
    :func:`~pypeeker.refactor.preconditions.evaluate_in_order`.
    """

    def __init__(
        self, code: str, detail: str, *, precondition: str | None = None
    ) -> None:
        """Store the machine code alongside the human-readable detail."""
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.precondition = precondition


def submit_intent(
    intent: Intent,
    store: IndexStore,
    tx_store: TransactionStore,
    *,
    default_error_code: str = "plan-refused",
) -> Materialized:
    """Submit ONE intent as a batch of one through the batch engine.

    Runs :func:`~pypeeker.refactor.batch.run_batch` over ``[intent]`` under
    :attr:`~pypeeker.refactor.batch.BatchPolicy.ALL_OR_NOTHING`, so the single
    intent either executes or the batch aborts — there is no partial outcome
    to report and no drop to swallow. ``run_batch`` schedules first, so
    malformed input (e.g. a ``deps`` entry naming an unknown intent id) is
    rejected the same way a real batch would reject it, before anything is
    planned; a scheduling failure raises :class:`SubmitError` with code
    ``"schedule-failed"``.

    ``tx_store`` is passed straight through as the batch's transaction store
    rather than being replaced by a scratch one, and that is the whole reason
    a batch of one is indistinguishable from a direct planner call: the
    intent's registered materializer re-plans through the underlying planner,
    which persists **its own** transaction in ``tx_store`` and builds its own
    :class:`~pypeeker.models.transaction.TransactionSummary`. That summary
    (and, for visibility ops, its ``warnings``) rides back out on
    :class:`~pypeeker.refactor.batch.ExecutedIntent` and is handed back here
    unchanged, so ``apply``/``rollback``/``transactions show`` work on it
    exactly as before and its ``operation`` is the planner's, never
    ``"batch"``. The same is true of the file-lifecycle channel
    (``files_created``/``files_deleted``, first populated by ``move-symbol``):
    the returned :class:`~pypeeker.refactor.registry.Materialized` describes
    the births and deaths as well as the edits, so a caller that simulates it
    through :func:`~pypeeker.refactor.batch.apply_to_overlay` — or classifies
    it, as ``check --fix``'s ``_touched_paths`` does — sees the whole
    mutation and not only its edit half.

    The engine plans against a throwaway
    :class:`~pypeeker.storage.overlay.OverlayIndexStore` layered over
    ``store``; a fresh overlay holds nothing, so every read falls through to
    ``store`` and the intent is planned against exactly the state a direct
    call would have seen. (The caller's ``store`` may itself be a simulation
    store — overlays nest, and reads delegate through the base store's own
    surface.) The subsequent in-memory splice still runs — it is the
    verification that every ``edit.old`` matches the bytes the planner just
    read — but the re-bind that would follow it is skipped
    (``rebind_final=False``): its only purpose is to let a *next* intent plan
    against this one's output, and a batch of one has none, while the overlay
    itself dies with this call. That matters because this function is the
    engine's hottest caller — ``check --fix`` submits a batch of one *per
    remedy* against the same store, so an unconditional re-bind would make it
    re-parse and re-bind every touched file once per fix.

    A refusal — a materializer miss, a returned failure string, or a splice
    mismatch — raises :class:`SubmitError`. The code is the refusing
    :class:`~pypeeker.refactor.registry.MaterializeError`'s own ``code`` when
    it set one (promote/demote's several refusal classes, the remedy
    planners' legacy slugs), else ``default_error_code`` — the single code
    every other builtin kind's CLI command has always used for every refusal.
    """
    try:
        result = run_batch(
            [intent],
            store,
            tx_store=tx_store,
            policy=BatchPolicy.ALL_OR_NOTHING,
            rebind_final=False,
        )
    except ScheduleError as error:
        raise SubmitError("schedule-failed", str(error)) from error
    except BatchAborted as error:
        # ALL_OR_NOTHING aborts on the first drop, so the triggering drop is
        # last — and for a batch of one it is the only one.
        refusal = error.dropped[-1]
        raise SubmitError(
            refusal.code or default_error_code,
            refusal.detail,
            precondition=refusal.precondition,
        ) from error
    executed = result.executed[0]
    return Materialized(
        edits=list(executed.edits),
        file_rename=executed.file_rename,
        summary=executed.summary,
        warnings=list(executed.warnings),
        files_created=dict(executed.files_created),
        files_deleted=list(executed.files_deleted),
    )


def submit_intents(
    intents: list[Intent],
    store: IndexStore,
    tx_store: TransactionStore,
    *,
    policy: BatchPolicy = BatchPolicy.SKIP_AND_REPORT,
    default_error_code: str = "plan-refused",
) -> Materialized | BatchResult:
    """Submit one or more intents; the plural entry point, one engine.

    Every submission runs through :func:`~pypeeker.refactor.batch.run_batch`
    (which schedules internally). What the count selects is not a code path
    but a **contract**, and the return type is how a caller reads it:

    * two or more intents are a batch — the caller gets the engine's
      :class:`~pypeeker.refactor.batch.BatchResult` unchanged and flattens it
      exactly as ``plan-batch`` does via
      :func:`~pypeeker.refactor.batch.flatten_batch`, then persists the result
      in ``tx_store``. The batch itself gets a **scratch** transaction store
      under a temp directory: its per-intent re-plans persist simulation
      intermediates that must not reach the caller's store, and the simulated
      state is in memory, so nothing outlives the directory;
    * a lone intent is a single submission and takes
      :func:`submit_intent`'s contract in full — the caller's own ``tx_store``
      (the planner's transaction is the durable output), all-or-nothing
      semantics, and the planner's own
      :class:`~pypeeker.refactor.registry.Materialized`. See its docstring for
      the error mapping it applies.

    Raises :class:`SubmitError` with code ``"no-intents"`` for an empty
    list; :func:`submit_intent`'s own exceptions propagate for a single
    intent, and :func:`~pypeeker.refactor.batch.run_batch`'s
    (:class:`~pypeeker.refactor.batch.ScheduleError`,
    :class:`~pypeeker.refactor.batch.ScheduleCycleError`,
    :class:`~pypeeker.refactor.batch.BatchAborted`) propagate unchanged for
    two or more, matching ``plan-batch``'s own error handling.
    """
    if not intents:
        raise SubmitError("no-intents", "submit_intents requires at least one intent")
    if len(intents) > 1:
        with tempfile.TemporaryDirectory(prefix="pypeeker-batch-") as scratch:
            return run_batch(
                intents, store, tx_store=TransactionStore(Path(scratch)), policy=policy
            )
    return submit_intent(
        intents[0], store, tx_store, default_error_code=default_error_code
    )
