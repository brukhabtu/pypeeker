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

Each remedy is submitted **individually, against the real store**, rather
than handing the whole set to :func:`~pypeeker.refactor.batch.run_batch`.
That is deliberate, because ``check --fix``'s contract is not the batch
engine's:

* the report distinguishes a **conflict** (two repairs whose *byte ranges*
  overlap — one is applied, the other reported under ``skipped_conflicts``)
  from a **refusal** (a planner declined, reported under ``declined``).
  ``run_batch``'s conflict model is footprint-level: every repair in one
  file writes that file, so a batch would serialize them and the later ones
  would either re-plan against an already-mutated mirror or drop as
  precondition failures — the wrong bucket, and a different set;
* every surviving repair is planned against **one** state (the pre-fix
  tree), which is what makes the ordering key ``(file, first edit offset,
  fix_id)`` stable and the whole run land as ONE ``check-fix`` transaction
  that a single ``rollback <tx_id>`` undoes.

The per-remedy planners persist their own transaction as a side effect of
planning; those go to a **throwaway** transaction store in a temp directory
(``_scratch_transactions``), so the only transaction that reaches the
project is the combined ``check-fix`` one this module writes and applies.
"""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pypeeker.app.submit import SubmitError, submit_intent
from pypeeker.check import CheckEngine, Violation
from pypeeker.models import Confidence, TransactionHeader
from pypeeker.refactor import ApplyError, Materialized, TransactionApplier
from pypeeker.storage import IndexStore, TransactionStore

__all__ = ["CheckFixApplyError", "apply_check_fixes"]


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


@dataclass
class _CheckFixOutcome:
    """The result of :func:`apply_check_fixes`.

    ``fixes`` are the remedies that made it into the transaction — applied,
    or merely written PENDING under ``plan_only``. ``apply_result`` is the
    :class:`~pypeeker.refactor.applier.TransactionApplier` result dict when
    the transaction was applied (``None`` under ``plan_only``, or when there
    was nothing to fix), so callers can report ``files_reindex_failed``
    rather than swallow it.

    ``residual`` is the FULL post-apply violation set (or the original
    ``violations`` when nothing was applied) — callers apply their own
    confidence display filter, matching plain ``check``'s behavior.
    """

    fixes: list[dict]
    skipped_conflicts: list[dict]
    declined: list[dict]
    residual: list[Violation]
    tx_id: str | None
    apply_result: dict | None = None


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


def apply_check_fixes(
    store: IndexStore,
    transaction_store: TransactionStore,
    engine: CheckEngine,
    violations: list[Violation],
    *,
    plan_only: bool = False,
) -> _CheckFixOutcome:
    """Plan, de-conflict, and apply violation-attached remedies (``check --fix``).

    * Only remedies on certain (DECLARED-confidence) findings are planned;
      heuristic/inferred/unknown findings never auto-apply.
    * Each remedy is submitted through :func:`~pypeeker.app.submit.submit_intent`
      against the real store; a planner refusal becomes a ``declined`` entry
      carrying the planner's stable code (``"ambiguous"``, ``"stale-index"``,
      ``"text-mismatch"``, ``"file-missing"``).
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

    Raises :class:`CheckFixApplyError` when the apply itself fails (the
    transaction was still written and stays inspectable).
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
