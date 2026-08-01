"""Planner registry: intent kind -> materializer (TASK-122).

Mirrors :func:`pypeeker.check.rules.register_rule`: instead of
``batch._materialize`` isinstance-dispatching over every concrete
:class:`~pypeeker.intents.Intent` subclass, each planner module registers
its own **materializer** — a callable with the exact signature and return
contract the isinstance branches used to have — under its intent's stable
``kind`` string. :func:`run_batch` (via ``batch._materialize``) becomes a
pure registry lookup, so adding a new intent kind never means editing
``batch.py``: the new planner module registers itself and imports it once
for the side effect (see the import block near the top of ``batch.py``).

A materializer takes ``(intent, store, tx_store)`` and returns either a
:class:`Materialized` (edits ready to splice into the simulation) or a ``str``
— the human-readable reason the intent's guards rejected the current
simulated state. It never raises for an *expected* planning failure (those
are caught and turned into the ``str`` return); an unexpected exception
propagates, exactly as it did inside the old isinstance branches. A
materializer with more than one distinct refusal code (currently only
``change-visibility``'s — see :mod:`pypeeker.refactor.visibility_ops`) may
return a :class:`MaterializeError` instead of a plain ``str``: it *is* a
``str`` (every ``isinstance(outcome, str)`` check below and in
:mod:`pypeeker.refactor.batch` keeps working unchanged), just one that also
carries the failing operation's stable ``code`` for a caller that needs it
(TASK-123's single-intent submit path, :mod:`pypeeker.app.submit`).

Registration is last-import-wins, mirroring :func:`register_rule`: a second
``@register_planner(kind)`` for the same kind silently replaces the first.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from pypeeker.intents import Intent
from pypeeker.models import EditEntry, FileRenameEntry, TransactionSummary
from pypeeker.storage import IndexStore, TransactionStore


class MaterializeError(str):
    """A materialization failure string carrying optional refusal metadata.

    Subclasses ``str`` so it satisfies the ``Materialized | str`` return
    contract unchanged — ``isinstance(outcome, str)`` and ``str(outcome)``
    both behave exactly as a plain failure string would. ``code`` lets a
    caller recover the exact machine-readable refusal class (e.g. a
    :class:`~pypeeker.refactor.visibility_ops.VisibilityOpError`'s ``code``)
    without changing the materializer contract; a materializer with a
    single failure code (every builtin kind except ``change-visibility``)
    has no reason to use this and may keep returning a plain ``str``.

    ``precondition`` (TASK-125, additive) names the failing
    :class:`~pypeeker.refactor.preconditions.Precondition`, when the planner
    that raised this refusal went through
    :func:`~pypeeker.refactor.preconditions.evaluate_in_order`. It is purely
    extra metadata for callers that want the named atom of "why not"
    (:class:`~pypeeker.refactor.batch.DroppedIntent`,
    :class:`~pypeeker.app.submit.SubmitError`) — nothing in this module reads
    it, and a materializer with no precondition to name leaves it ``None``.
    """

    code: str | None
    precondition: str | None

    def __new__(
        cls, message: str, *, code: str | None = None, precondition: str | None = None
    ) -> "MaterializeError":
        """Build the failure string and stash ``code``/``precondition`` as extra attributes."""
        obj = super().__new__(cls, message)
        obj.code = code
        obj.precondition = precondition
        return obj


@dataclass
class Materialized:
    """A successful guarded re-plan: the edits to apply at this turn.

    ``summary``/``warnings`` are populated only by materializers whose
    underlying planner also produces a planner-native
    :class:`~pypeeker.models.transaction.TransactionSummary` (every builtin
    kind except ``edit`` and ``delete-symbol``): the single-intent submit path
    (:mod:`pypeeker.app.submit`) reads them to reproduce the exact JSON a
    direct planner call used to emit. Neither field drives the simulation —
    only ``edits``/``file_rename`` do — but since TASK-129 collapsed submit
    onto the batch engine, :func:`~pypeeker.refactor.batch.run_batch` *carries*
    both out on :class:`~pypeeker.refactor.batch.ExecutedIntent` so a batch of
    one can still hand back the planner's own summary.

    ``files_created``/``files_deleted`` (TASK-131) are the file-lifecycle
    channel: a planner declares a *birth* by mapping the new path to its full
    content bytes, and a *death* by listing the path. The engine
    (:func:`~pypeeker.refactor.batch._apply_to_overlay`) turns them into
    ``overlay.write_file`` / ``overlay.delete_file`` + ``overlay.remove``.
    Content is bytes, not ``str``, because that is what the simulation
    substrate and the byte-exact diff behind
    :func:`~pypeeker.refactor.batch.flatten_store` speak; the transaction
    entry's ``str`` payload is produced at flatten time. Both default empty,
    so every pre-existing materializer is unchanged, and neither is a
    *licence*: :func:`~pypeeker.refactor.batch.flatten_store` still refuses
    any birth or death the intent's
    :class:`~pypeeker.intents.footprint.Effect` did not also declare.
    """

    edits: list[EditEntry] = field(default_factory=list)
    file_rename: FileRenameEntry | None = None
    summary: TransactionSummary | None = None
    warnings: list[str] = field(default_factory=list)
    files_created: dict[str, bytes] = field(default_factory=dict)
    files_deleted: list[str] = field(default_factory=list)


Materializer = Callable[[Intent, IndexStore, TransactionStore], "Materialized | str"]
"""``(intent, store, tx_store) -> Materialized | str``, one per intent kind."""

_REGISTRY: dict[str, Materializer] = {}


def register_planner(kind: str) -> Callable[[Materializer], Materializer]:
    """Register ``kind``'s materializer (decorator), mirroring ``register_rule``.

    A second registration for the same ``kind`` replaces the first (last
    import wins) — the same precedence :func:`pypeeker.check.rules.register_rule`
    gives custom rules among themselves.
    """

    def _decorate(materializer: Materializer) -> Materializer:
        _REGISTRY[kind] = materializer
        return materializer

    return _decorate


def get_materializer(kind: str) -> Materializer | None:
    """The materializer registered for ``kind``, or ``None`` on a miss."""
    return _REGISTRY.get(kind)


def load_transaction(tx_store: TransactionStore, tx_id: str) -> Materialized:
    """The edits a planner just persisted for ``tx_id``, as a materialization.

    Shared by every planner-backed materializer: each planner's ``plan()``
    persists a transaction and returns a summary carrying its id; this turns
    that persisted transaction back into the edits/file-rename pair
    ``run_batch`` splices into the simulation overlay.

    A persisted :class:`~pypeeker.models.transaction.FileCreateEntry` /
    :class:`~pypeeker.models.transaction.FileDeleteEntry` comes back through
    the same door, as ``files_created``/``files_deleted`` — so a planner that
    plans a file birth or death is simulatable with no per-planner wiring.
    No builtin planner emits either today, which is why this is a pure
    widening.
    """
    loaded = tx_store.load(tx_id)
    if loaded is None:  # pragma: no cover - planners always persist what they return
        raise RuntimeError(f"planner reported transaction '{tx_id}' but none exists")
    return Materialized(
        edits=loaded.edits,
        file_rename=loaded.file_rename,
        files_created={
            create.path: create.content.encode("utf-8") for create in loaded.creates
        },
        files_deleted=[delete.path for delete in loaded.deletes],
    )
