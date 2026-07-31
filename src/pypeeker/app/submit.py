"""Application service: the single execution pipeline for mutating CLI commands.

TASK-123 ("everything is a batch"): every mutating CLI command builds one or
more :class:`~pypeeker.intents.Intent` objects and submits them here instead
of constructing a planner and calling ``plan()`` directly — a lone refactor
is a batch of one (see architecture.md's target-architecture section, item
3). :func:`submit_intent` always runs
:func:`~pypeeker.refactor.batch.schedule` first — even for a single intent —
so conflict/cycle validation is uniform across every entry point, then
materializes directly against the REAL store/transaction store: the intent's
registered materializer (:mod:`pypeeker.refactor.registry`) re-plans through
the underlying planner, which persists its own transaction and (for every
builtin planner-backed kind) attaches its own
:class:`~pypeeker.models.transaction.TransactionSummary` to the returned
:class:`~pypeeker.refactor.registry.Materialized` — so a CLI command that
echoes ``materialized.summary`` gets output byte-identical to a direct
planner call, because it *is* one, just reached through the scheduler first.

:func:`submit_intents` is the plural entry point the design calls for:
a single intent takes :func:`submit_intent`'s real-store fast path; two or
more delegate to the existing :func:`~pypeeker.refactor.batch.run_batch` /
:func:`~pypeeker.refactor.batch.flatten_batch` pair — the same path
``plan-batch`` uses today, unchanged. No builtin CLI command submits more
than one intent yet (that remains ``plan-batch``'s own job), but the
dispatch lives here so the pipeline has one entry point regardless of count.

Layering: this module lives in ``app`` (not ``refactor``) by the same
convention every other CLI-facing composition here follows (see
``check_fixes.py``, ``privatize.py``), not because it needs both ``check``
and ``refactor`` — it only needs ``refactor``/``intents``/``storage``.
"""

from __future__ import annotations

from pathlib import Path

from pypeeker.intents import Intent
from pypeeker.refactor import (
    BatchPolicy,
    BatchResult,
    Materialized,
    ScheduleError,
    get_materializer,
    run_batch,
    schedule,
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
    """Submit ONE intent as a batch of one; materialize it against the real store.

    Always schedules first (:func:`~pypeeker.refactor.batch.schedule` over
    ``[intent]``), so malformed input — e.g. a ``deps`` entry naming an
    unknown intent id — is rejected the same way a real batch would reject
    it, even though a lone intent can never hit a footprint conflict or
    dependency cycle with itself. A scheduling failure raises
    :class:`SubmitError` with code ``"schedule-failed"``.

    After a clean schedule, looks up ``intent.kind``'s registered
    materializer (:func:`~pypeeker.refactor.registry.get_materializer`) and
    calls it directly against ``store``/``tx_store`` — the real stores, not
    a mirror — so a successful materialization is the underlying planner's
    own ``plan()`` call, persisted for real: the returned
    :class:`~pypeeker.refactor.registry.Materialized` carries the planner's
    own ``summary`` (and, for visibility ops, ``warnings``) whenever the
    materializer sets one. A materializer miss or a returned failure string
    both raise :class:`SubmitError`: the code is taken from a
    :class:`~pypeeker.refactor.registry.MaterializeError`'s attached
    ``code`` when the materializer set one (promote/demote's several
    refusal classes), else ``default_error_code`` — the single code every
    other builtin kind's CLI command has always used for every refusal.
    """
    try:
        schedule([intent], store)
    except ScheduleError as error:
        raise SubmitError("schedule-failed", str(error)) from error

    materializer = get_materializer(intent.kind)
    if materializer is None:
        raise SubmitError(
            default_error_code, f"no executor for intent kind '{intent.kind}'"
        )
    outcome = materializer(intent, store, tx_store)
    if isinstance(outcome, str):
        code = getattr(outcome, "code", None) or default_error_code
        precondition = getattr(outcome, "precondition", None)
        raise SubmitError(code, str(outcome), precondition=precondition)
    return outcome


def submit_intents(
    intents: list[Intent],
    store: IndexStore,
    tx_store: TransactionStore,
    *,
    policy: BatchPolicy = BatchPolicy.SKIP_AND_REPORT,
    work_dir: Path | None = None,
    default_error_code: str = "plan-refused",
) -> Materialized | BatchResult:
    """Submit one or more intents through the batch engine, singles inline.

    A single intent takes :func:`submit_intent`'s real-store fast path (see
    its docstring for the full contract, including the scheduling and error
    mapping it always applies). Two or more intents delegate to
    :func:`~pypeeker.refactor.batch.run_batch` (which schedules internally),
    returning its :class:`~pypeeker.refactor.batch.BatchResult` unchanged —
    the caller flattens it exactly as ``plan-batch`` does via
    :func:`~pypeeker.refactor.batch.flatten_batch`.

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
    if len(intents) == 1:
        return submit_intent(
            intents[0], store, tx_store, default_error_code=default_error_code
        )
    return run_batch(intents, store, policy=policy, work_dir=work_dir)
