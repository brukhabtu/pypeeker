"""The ``"delete-symbol"`` materializer (TASK-122).

Kept in its own module rather than in :mod:`pypeeker.refactor.delete` for a
historical reason worth stating: this file used to hold the materializers
that had no planner behind them at all. ``"delete-symbol"`` was one of them
(v1's stub always reported why it could not execute) and ``"edit"`` — the
superseded TASK-82 fix protocol wrapped as an intent — was the other. The
fix protocol is gone (TASK-124: rules now attach real intents, and the five
repairs are real planners), and ``delete-symbol`` acquired
:class:`~pypeeker.refactor.delete.DeleteSymbolPlanner`, so this module is a
thin registration shim over that planner with the same guarded-re-validation
contract every other planner-backed materializer has.
"""

from __future__ import annotations

from pypeeker.intents import DeleteSymbolIntent
from pypeeker.refactor.delete import DeleteSymbolError, DeleteSymbolPlanner
from pypeeker.refactor.plan_support import simple_materializer
from pypeeker.refactor.registry import register_planner

# Same guarded-re-validation contract every registered materializer has (see
# :mod:`pypeeker.refactor.registry`): a refusal is a
# :class:`~pypeeker.refactor.registry.MaterializeError` carrying the rejecting
# :class:`~pypeeker.refactor.delete.DeleteSymbolError`'s stable refusal code —
# the same distinguishing-code pattern :mod:`pypeeker.refactor.visibility_ops`
# established for ``change-visibility``, and what ``check --fix`` reads to
# report the refusal reason.
_materialize_delete_symbol = register_planner(DeleteSymbolIntent.kind)(
    simple_materializer(
        DeleteSymbolIntent,
        DeleteSymbolPlanner,
        DeleteSymbolError,
        lambda intent: (intent.anchor,),
    )
)
