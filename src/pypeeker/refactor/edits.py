"""Materializers with no dedicated planner module (TASK-122).

Two intent kinds have nowhere else to self-register a planner-registry
materializer (see :mod:`pypeeker.refactor.registry`):

* ``"edit"`` (:class:`~pypeeker.intents.FixIntent`) — a TASK-82 fix-protocol
  edit wrapped as an intent; there is no planner class, just the fix's own
  ``plan()``.
* ``"delete-symbol"`` (:class:`~pypeeker.intents.DeleteSymbolIntent`) — has
  no planner in v1 at all; the intent is schedulable (ordering/remap
  participate via its footprint/effect) but never executable, so its
  materializer always reports why.
"""

from __future__ import annotations

from pypeeker.intents import DeleteSymbolIntent, FixIntent, Intent
from pypeeker.refactor.registry import Materialized, register_planner
from pypeeker.storage import IndexStore, TransactionStore


@register_planner(FixIntent.kind)
def _materialize_fix(
    intent: Intent, store: IndexStore, tx_store: TransactionStore
) -> Materialized | str:
    """Plan a :class:`FixIntent` through its wrapped fix's own ``plan()``.

    Unlike the planner-backed materializers, a fix's ``plan()`` isn't
    wrapped in a try/except here — it never raised in the old isinstance
    branch either, so an unexpected exception still propagates.
    """
    assert isinstance(intent, FixIntent)
    del tx_store  # fixes persist nothing through the transaction store
    result = intent.fix.plan(store)
    edits = getattr(result, "edits", None)
    if edits is None:
        detail = getattr(result, "reason", "") or "fix declined to plan"
        return f"fix '{intent.fix.fix_id}' declined: {detail}"
    return Materialized(edits=list(edits))


@register_planner(DeleteSymbolIntent.kind)
def _materialize_delete_symbol(
    intent: Intent, store: IndexStore, tx_store: TransactionStore
) -> Materialized | str:
    """``delete-symbol`` has no planner in v1: always report why."""
    del intent, store, tx_store
    return (
        "delete-symbol has no planner in v1; the intent is schedulable "
        "(ordering/remap) but not executable"
    )
