"""Application service: run a multi-intent batch and persist ONE flattened transaction.

The workflow behind the ``batch`` command, minus its Click parsing and JSON
printing: submit the intents through :func:`~pypeeker.app.submit.submit_intents`
(the batch contract, whatever the count), flatten the simulation's net change
via :func:`~pypeeker.refactor.batch.flatten_batch`, and write the flattened
transaction into the caller's store when there is anything to write. The
CLI only decides how to render the returned report and which exit code a
refusal earns.
"""

from __future__ import annotations

from pypeeker.app.submit import submit_intents
from pypeeker.intents import Intent
from pypeeker.refactor import BatchPolicy, flatten_batch
from pypeeker.storage import IndexStore, TransactionStore

__all__ = ["dropped_intent_report", "run_intent_batch"]


def dropped_intent_report(dropped: object) -> dict:
    """Shape one :class:`~pypeeker.refactor.batch.DroppedIntent` for JSON output.

    ``{"id", "reason", "detail"}`` — the same entry ``batch`` and
    ``privatize`` both print under ``dropped``, named once so the two reports
    cannot drift.
    """
    return {
        "id": dropped.intent.intent_id,
        "reason": dropped.reason.value,
        "detail": dropped.detail,
    }


def run_intent_batch(
    intents: list[Intent],
    store: IndexStore,
    transaction_store: TransactionStore,
    *,
    policy: BatchPolicy,
) -> dict:
    """Simulate ``intents`` as one batch and persist the flattened transaction.

    Returns the ``batch`` report as plain data — ``{tx_id, executed, dropped,
    files_affected, edit_count, files_created, files_deleted}``. ``tx_id`` is
    ``None`` when the batch was a net no-op (no edits, creations or
    deletions), in which case nothing was written to ``transaction_store``;
    ``executed`` is empty when every intent dropped, which the caller
    reports as its own refusal — the flattened transaction of an empty batch
    is empty too, so nothing is persisted for that case either.

    Raises :class:`~pypeeker.refactor.batch.BatchAborted` (under
    :attr:`~pypeeker.refactor.batch.BatchPolicy.ALL_OR_NOTHING`),
    :class:`~pypeeker.refactor.batch.ScheduleError` and
    :class:`~pypeeker.refactor.batch.FlattenError` unchanged; the caller maps
    each to its error envelope.
    """
    # ``always_batch``: a one-entry intents file still gets a BatchResult.
    result = submit_intents(
        intents, store, transaction_store, policy=policy, always_batch=True
    )
    flat = flatten_batch(result, store)
    dropped = [dropped_intent_report(d) for d in result.dropped]
    # Pass the flattened create/delete entries through explicitly: a
    # transaction whose edits assume a file the batch also creates is only
    # applicable if the creation travels with them.
    header, edits = flat.header, flat.edits
    tx_id = None
    if edits or flat.creates or flat.deletes:
        transaction_store.save(header, edits, creates=flat.creates, deletes=flat.deletes)
        tx_id = header.tx_id
    return {
        "tx_id": tx_id,
        "executed": [
            {"id": e.intent.intent_id, "kind": e.intent.kind} for e in result.executed
        ],
        "dropped": dropped,
        "files_affected": sorted({edit.file for edit in edits}),
        "edit_count": len(edits),
        "files_created": sorted(entry.path for entry in flat.creates),
        "files_deleted": sorted(entry.path for entry in flat.deletes),
    }
