"""Application service: the ``privatize`` command's mass-demotion workflow.

Wires the demotion-feeding check rules to the batch demotion planner: load
the project config, run the selected rules, extract the nominated symbols
from the findings, plan ONE flattened demotion transaction, and — optionally
— apply it. Lives in ``app`` (not ``check`` or ``refactor``) because it needs
both: ``check`` to run rules and extract findings, ``refactor`` to plan and
apply the transaction, and ``refactor`` may not import ``check``.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path

from pypeeker.check import CheckEngine, load_config
from pypeeker.check.demotion import DEMOTION_RULES, demote_entry
from pypeeker.refactor import (
    ApplyError,
    CandidateEntry,
    TransactionApplier,
    PrivatizeOutcome,
    plan_privatize,
)
from pypeeker.storage import IndexStore, TransactionStore

__all__ = ["run_privatize"]


@dataclass
class _PrivatizeReport:
    """The result of :func:`run_privatize`.

    ``outcome`` is the batch planner's report (tx summary, executed,
    dropped, skipped, warnings — see
    :class:`~pypeeker.refactor.privatize.PrivatizeOutcome`). ``applied`` is
    the apply result dict when ``apply_plan`` was requested and the apply
    succeeded; ``apply_error`` carries the message when it was requested but
    failed (the transaction stays on disk either way).
    """

    outcome: PrivatizeOutcome
    applied: dict | None = None
    apply_error: str | None = None


def run_privatize(
    store: IndexStore,
    transaction_store: TransactionStore,
    root: Path,
    rules: tuple[str, ...] = (),
    *,
    apply_plan: bool = False,
    skip_heuristic: bool = True,
) -> _PrivatizeReport:
    """Plan (and optionally apply) a mass demotion driven by check findings.

    Runs the selected demotion-feeding rules (``rules``, or every rule in
    :data:`~pypeeker.check.demotion.DEMOTION_RULES` when empty) with the
    project's configured options — injecting ``[tool.pypeeker.visibility]``
    for any selected rule the pyproject does not otherwise enable — extracts
    the nominated symbols via :func:`~pypeeker.check.demotion.demote_entry`,
    and plans them as one batch through
    :func:`~pypeeker.refactor.privatize.plan_privatize`.

    With ``apply_plan=True`` and a plannable outcome, the transaction is
    applied immediately via :class:`~pypeeker.refactor.applier.
    TransactionApplier`; an :class:`~pypeeker.refactor.applier.ApplyError` is
    caught and reported on the returned :class:`_PrivatizeReport` rather than
    raised, so callers can still surface the rest of the report (dropped/
    skipped/warnings) alongside the failure.
    """
    selected = tuple(dict.fromkeys(rules)) or DEMOTION_RULES
    base = load_config(root)
    # load_config injects the [tool.pypeeker.visibility] table only into the
    # rules the pyproject enables; the selected rules here may not be among
    # them, so inject the parsed config ourselves (setdefault keeps any
    # explicit per-rule override and the injected raw table intact).
    rule_options = {name: dict(opts) for name, opts in base.rule_options.items()}
    for name in selected:
        rule_options.setdefault(name, {}).setdefault("visibility", base.visibility)
    config = dataclasses.replace(base, rules=selected, rule_options=rule_options)

    violations = CheckEngine(store, config).run()
    entries: list[CandidateEntry] = []
    for violation in violations:
        entry = demote_entry(violation)
        if entry is not None:
            entries.append(entry)

    outcome = plan_privatize(
        store,
        transaction_store,
        entries,
        skip_heuristic=skip_heuristic,
    )

    applied: dict | None = None
    apply_error: str | None = None
    if apply_plan and outcome.summary is not None:
        try:
            applied = TransactionApplier(store, transaction_store).apply(
                outcome.summary.tx_id
            )
        except ApplyError as e:
            apply_error = str(e)

    return _PrivatizeReport(outcome=outcome, applied=applied, apply_error=apply_error)
