"""Application service: plan, de-conflict, and apply ``check --fix`` findings.

Sits above :mod:`pypeeker.check` and :mod:`pypeeker.refactor` — it is the one
place allowed to import both, because ``check`` may not import ``refactor``
(see the ``import-boundaries`` layering). The CLI's ``check --fix`` handler
calls :func:`apply_check_fixes` and does nothing but format the result and
choose an exit code, which is what makes this workflow testable without
spawning the CLI through :class:`click.testing.CliRunner`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from pypeeker.check import CheckEngine, Violation
from pypeeker.check.fixes import FixPlan
from pypeeker.models.capabilities import Confidence
from pypeeker.models.transaction import TransactionHeader
from pypeeker.refactor import ApplyError, TransactionApplier
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

    ``residual`` is the FULL post-apply violation set (or the original
    ``violations`` when nothing was applied) — callers apply their own
    confidence display filter, matching plain ``check``'s behavior.
    """

    applied: list[dict]
    skipped_conflicts: list[dict]
    declined: list[dict]
    residual: list[Violation]
    tx_id: str | None


def auto_fixable(violation: Violation) -> bool:
    """Whether a finding's fix may be applied without human review.

    The single auto-fix eligibility policy, shared by ``check --fix`` and
    ``plan-batch``'s ``fix`` intent expansion: the finding must carry a fix
    and be certain (DECLARED confidence) — heuristic/inferred/unknown
    findings never auto-apply.
    """
    return violation.fix is not None and violation.confidence is Confidence.DECLARED


def _order(item: tuple[Violation, FixPlan]) -> tuple[str, int, str]:
    """Deterministic application order: earliest edit, then fix_id."""
    _, plan = item
    first = min((edit.file, edit.start) for edit in plan.edits)
    return (first[0], first[1], plan.fix_id)


def apply_check_fixes(
    store: IndexStore,
    transaction_store: TransactionStore,
    engine: CheckEngine,
    violations: list[Violation],
) -> _CheckFixOutcome:
    """Plan, de-conflict, and apply violation-attached fixes (``check --fix``).

    * Only fixes on certain (DECLARED-confidence) findings are planned;
      heuristic/inferred/unknown findings never auto-apply.
    * Planned fixes are considered in deterministic (file, start, fix_id)
      order; a fix whose byte ranges overlap an already-kept fix in the same
      file is skipped as a conflict — one fix per file region, across rules.
    * The surviving edits are written as ONE ``check-fix`` transaction and
      applied immediately through :class:`~pypeeker.refactor.applier.
      TransactionApplier`, so the standard lifecycle holds: hashes are
      verified before writing, edited files are re-indexed, and the APPLIED
      transaction stays on disk for ``rollback <tx_id>`` /
      ``transactions show <tx_id>``.
    * ``engine`` is re-run after the apply to compute the residual count.

    Raises :class:`CheckFixApplyError` when the apply itself fails (the
    transaction was still written and stays inspectable).
    """
    declined: list[dict] = []
    planned: list[tuple[Violation, FixPlan]] = []
    for violation in violations:
        if not auto_fixable(violation):
            continue
        outcome = violation.fix.plan(store)
        if isinstance(outcome, FixPlan):
            planned.append((violation, outcome))
        else:
            declined.append(
                {
                    "fix_id": outcome.fix_id,
                    "reason": outcome.reason.value,
                    "detail": outcome.detail,
                }
            )

    planned.sort(key=_order)
    applied: list[dict] = []
    skipped_conflicts: list[dict] = []
    kept_plans: list[FixPlan] = []
    claimed: dict[str, list[tuple[int, int]]] = {}
    for violation, plan in planned:
        entry = {
            "fix_id": plan.fix_id,
            "description": plan.description,
            "violation": str(violation),
        }
        conflicts = any(
            edit.start < end and start < edit.end
            for edit in plan.edits
            for start, end in claimed.get(edit.file, ())
        )
        if conflicts:
            skipped_conflicts.append(entry)
            continue
        kept_plans.append(plan)
        applied.append(entry)
        for edit in plan.edits:
            claimed.setdefault(edit.file, []).append((edit.start, edit.end))

    tx_id: str | None = None
    if kept_plans:
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
            header, [edit for plan in kept_plans for edit in plan.edits]
        )
        try:
            TransactionApplier(store, transaction_store).apply(tx_id)
        except ApplyError as e:
            raise CheckFixApplyError(str(e), tx_id) from e
        residual = engine.run()  # the applier re-indexed the edited files
    else:
        residual = violations

    return _CheckFixOutcome(
        applied=applied,
        skipped_conflicts=skipped_conflicts,
        declined=declined,
        residual=residual,
        tx_id=tx_id,
    )
