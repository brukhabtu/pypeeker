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
:class:`Materialized` (edits ready to splice into the mirror) or a ``str``
— the human-readable reason the intent's guards rejected the current
simulated state. It never raises for an *expected* planning failure (those
are caught and turned into the ``str`` return); an unexpected exception
propagates, exactly as it did inside the old isinstance branches.

Registration is last-import-wins, mirroring :func:`register_rule`: a second
``@register_planner(kind)`` for the same kind silently replaces the first.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from pypeeker.intents import Intent
from pypeeker.models import EditEntry, FileRenameEntry
from pypeeker.storage import IndexStore, TransactionStore


@dataclass
class Materialized:
    """A successful guarded re-plan: the edits to apply at this turn."""

    edits: list[EditEntry] = field(default_factory=list)
    file_rename: FileRenameEntry | None = None


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
    ``run_batch`` splices into the mirror.
    """
    loaded = tx_store.load(tx_id)
    if loaded is None:  # pragma: no cover - planners always persist what they return
        raise RuntimeError(f"planner reported transaction '{tx_id}' but none exists")
    _, edits, file_rename = loaded
    return Materialized(edits=edits, file_rename=file_rename)
