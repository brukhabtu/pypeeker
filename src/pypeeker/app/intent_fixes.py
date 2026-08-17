"""Application service: plan, de-conflict, and apply a flat list of repair intents.

The engine-agnostic half of ``check --fix``. :mod:`pypeeker.app.check_fixes`
does the same work over :class:`~pypeeker.check.models.Violation` objects,
reaching their remedies through ``violation.remedy`` and gating them on
``auto_fixable``; this module takes the intents themselves and knows nothing
about where they came from. That is the whole difference: the DSL's mutation
terminals decide *whether* a row earns a repair — the confidence floor is an
attribute of the mutation value (fork #2), so it has already been applied by
the time an intent exists — and what is left is the part that was never about
rules at all, turning a set of intents into ONE ``check-fix`` transaction.

**A deliberate re-implementation, not a call.** This does not delegate to
``app/check_fixes.py:_plan_pass``, and the duplication is sanctioned for the
reason ``dsl/differential.py:_read_config`` already documents: the new side
must never execute frozen-path code, or the oracle would be grading a thing
against itself. The frozen pass stays the executable spec; this is the copy
under test, and ``scripts/differential-check.py``'s fix pass is what proves
the two agree.

What is reproduced, clause for clause, from ``_plan_pass`` and from
:func:`~pypeeker.app.check_fixes.apply_check_fixes`'s ``max_iterations == 1``
branch:

* every intent is submitted **individually** through
  :func:`~pypeeker.app.submit.submit_intent` — a batch of one, whose planner
  re-validates its preconditions against the current file bytes — rather than
  handed to :func:`~pypeeker.refactor.batch.run_batch` as a set, because
  ``check --fix``'s report distinguishes a byte-range **conflict** from a
  planner **refusal** and the batch engine's footprint-level conflict model
  does not;
* the per-intent planners' own transactions go to a **throwaway** store under
  a temp directory, so the only transaction that reaches the project is the
  combined ``check-fix`` one written here;
* survivors are ordered by ``(file, first edit offset, intent_id)`` and a
  repair whose byte ranges overlap an already-kept one is reported under
  ``skipped_conflicts`` rather than applied;
* the kept edits land as ONE ``check-fix``
  :class:`~pypeeker.models.TransactionHeader` that a single ``rollback
  <tx_id>`` undoes, applied immediately unless ``plan_only`` leaves it PENDING.

**Scope.** Only the single pass is ported. ``--fix-until-clean``'s bounded
fixpoint — the overlay, the re-run of the rules against simulated state, the
flatten — is a superset path over this same pass and lands at the flip; see
``dsl-rewrite.md``'s divergence ledger. Nothing here computes a residual
count either: that is a second whole-engine run, and which engine to re-run is
the caller's question, not this pass's.

This module imports neither ``pypeeker.check`` nor ``pypeeker.dsl``, so
``app``'s import-boundaries allow-list is unchanged by phase 4. The
composition of a DSL rule run with this pass happens in
``scripts/dsl-fix-engine.py``, outside ``src/`` and outside the boundary table.
"""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pypeeker.app.submit import SubmitError, submit_intent
from pypeeker.intents import Intent
from pypeeker.models import TransactionHeader
from pypeeker.refactor import ApplyError, Materialized, TransactionApplier
from pypeeker.storage import IndexStore, TransactionStore

__all__ = ["DuplicateIntentIdError", "IntentFixOutcome", "plan_intent_fixes"]


class DuplicateIntentIdError(ValueError):
    """Two intents in one pass carry the same ``intent_id``.

    Fork #5 makes an intent's id purely derived —
    ``<origin>:<mutation>:<anchor>`` — so two equal ids mean two rows claimed
    the same anchor for the same repair. That is a rule-authoring bug, and it
    must not be allowed to look like an ordinary outcome: the second submit
    would plan the same edit again, lose the byte-range check to its own twin,
    and surface as a phantom ``skipped_conflicts`` entry indistinguishable
    from two *different* repairs colliding. Refused loudly instead, naming the
    id.

    Unreachable today — the ported rules' anchors are unique per row — which
    is precisely why the guard is cheap to keep.
    """


@dataclass
class IntentFixOutcome:
    """The result of :func:`plan_intent_fixes`.

    ``fixes`` are the repairs that made it into the transaction — applied, or
    written PENDING under ``plan_only``; ``fixes`` is non-empty **iff**
    ``tx_id`` is set, and entry-wise the transaction *is* their edits.
    ``skipped_conflicts`` are the repairs whose byte ranges overlapped an
    already-kept one, and ``declined`` the ones whose planner refused, each
    carrying that planner's stable code.

    ``apply_result`` is the
    :class:`~pypeeker.refactor.applier.TransactionApplier` result dict when the
    transaction was applied — ``None`` under ``plan_only``, or when there was
    nothing to fix — so a caller can report ``files_reindex_failed`` rather
    than swallow it.

    Deliberately absent: a residual violation set. Computing one means running
    a whole check engine again, and this pass is engine-agnostic by design.
    """

    fixes: list[dict] = field(default_factory=list)
    skipped_conflicts: list[dict] = field(default_factory=list)
    declined: list[dict] = field(default_factory=list)
    tx_id: str | None = None
    apply_result: dict | None = None


@contextmanager
def _scratch_transactions() -> Iterator[TransactionStore]:
    """A throwaway :class:`TransactionStore` under a temp directory.

    Every planner persists the transaction it plans; this pass wants only the
    ONE combined ``check-fix`` transaction on disk, so the per-intent ones are
    written here and discarded with the directory.
    """
    with tempfile.TemporaryDirectory(prefix="pypeeker-intent-fix-") as tmp:
        yield TransactionStore(Path(tmp))


def _order(item: tuple[Intent, Materialized]) -> tuple[str, int, str]:
    """Deterministic application order: earliest edit, then intent id."""
    intent, materialized = item
    first = min((edit.file, edit.start) for edit in materialized.edits)
    return (first[0], first[1], intent.intent_id)


def _require_unique_ids(intents: Iterable[Intent]) -> list[Intent]:
    """Return ``intents`` as a list, refusing a repeated ``intent_id``."""
    ordered = list(intents)
    seen: set[str] = set()
    for intent in ordered:
        if intent.intent_id in seen:
            raise DuplicateIntentIdError(
                f"two repairs in one pass carry the intent id "
                f"{intent.intent_id!r}; ids are derived as "
                "'<origin>:<mutation>:<anchor>' and must be unique per pass"
            )
        seen.add(intent.intent_id)
    return ordered


def plan_intent_fixes(
    store: IndexStore,
    transaction_store: TransactionStore,
    intents: Iterable[Intent],
    *,
    plan_only: bool = False,
) -> IntentFixOutcome:
    """Plan, de-conflict, and apply ``intents`` as one ``check-fix`` transaction.

    Args:
        store: the index store the repairs are planned against; the planners
            read current file bytes through it, and the applier re-indexes
            edited files into it.
        transaction_store: where the ONE combined ``check-fix`` transaction is
            written. The per-intent planner transactions never land here.
        intents: the repairs to attempt, flat and unordered — exactly what
            :meth:`pypeeker.dsl.Application.intents` yields. Order does not
            matter: the pass imposes its own ``(file, start, intent_id)``
            ordering before de-conflicting, which is what makes the outcome a
            function of the intent *set*.
        plan_only: write the transaction PENDING and touch no file. Everything
            else — the planning, the refusals, the conflict resolution — is
            identical, which is what makes ``--plan`` a preview rather than a
            different code path.

    Returns:
        The :class:`IntentFixOutcome` describing what landed, what lost a
        byte-range conflict, and what a planner refused.

    Raises:
        DuplicateIntentIdError: two intents share a derived id.
        ApplyError: the apply itself failed. The transaction was still written
            and stays inspectable through ``transactions show <tx_id>``.
    """
    ordered = _require_unique_ids(intents)

    declined: list[dict] = []
    planned: list[tuple[Intent, Materialized]] = []
    with _scratch_transactions() as scratch:
        for intent in ordered:
            try:
                materialized = submit_intent(intent, store, scratch)
            except SubmitError as error:
                declined.append(
                    {
                        "fix_id": intent.intent_id,
                        "reason": error.code,
                        "detail": error.detail,
                    }
                )
            else:
                planned.append((intent, materialized))

    planned.sort(key=_order)
    fixes: list[dict] = []
    skipped_conflicts: list[dict] = []
    kept: list[Materialized] = []
    claimed: dict[str, list[tuple[int, int]]] = {}
    for intent, materialized in planned:
        entry = {"fix_id": intent.intent_id, "description": intent.description}
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

    outcome = IntentFixOutcome(
        fixes=fixes, skipped_conflicts=skipped_conflicts, declined=declined
    )
    if not kept:
        return outcome

    outcome.tx_id = uuid.uuid4().hex[:12]
    header = TransactionHeader(
        tx_id=outcome.tx_id,
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
            outcome.apply_result = TransactionApplier(store, transaction_store).apply(
                outcome.tx_id
            )
        except ApplyError:
            # The transaction is on disk and inspectable; the caller decides
            # whether a failed apply is fatal. Re-raised unchanged rather than
            # wrapped, because unlike `check --fix` this pass has no CLI
            # envelope of its own to carry a wrapper's code into.
            raise
    return outcome
