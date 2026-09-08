"""Application service: the ``privatize`` command's workflow (TASK-157).

Nominate symbols with the three demotion rules, then plan ONE flattened
demotion transaction. The nomination, the confidence gate and the two pointwise
guards all come off :data:`pypeeker.dsl.DEMOTE` rather than being restated
here and in ``refactor.privatize``'s pre-filter.

Three things this service deliberately does *not* do, each of which the frozen
``app/privatize.py`` it replaced did:

* **No ``apply_plan``.** The parameter existed on the frozen service and was
  already dead from the CLI — ``cli.py``'s ``_finish_mutation`` owns applying
  a planned transaction. Carrying it forward would ship a permanently ``None``
  ``applied``/``apply_error`` pair.
* **No message parsing.** The frozen demotion module (deleted at the flip) recovered each
  candidate's symbol id by running three regexes over the finding's *message
  text*, which made rule wording a load-bearing contract. A DSL row knows its
  own anchor.
* **No second heuristic filter.** The frozen ``skip_heuristic`` is
  :data:`~pypeeker.dsl.DEMOTE`'s ``INFERRED`` floor, so a row weakened by
  dynamic access is refused before an intent exists and arrives here as a
  :class:`~pypeeker.dsl.MutationDecision` reason, not as a confidence string
  for the pre-filter to re-interpret.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pypeeker.dsl import (
    BELOW_FLOOR,
    DEMOTION_RULES,
    PROJECT_VISIBILITY_KEY,
    Corpus,
    MutationDecision,
    install_expressions,
    privatize_selections,
    read_config,
    read_visibility_table,
)
from pypeeker.intents import ChangeVisibilityIntent
from pypeeker.refactor import PrivatizeOutcome, SkippedSymbol, plan_privatize
from pypeeker.storage import IndexStore, TransactionStore

__all__ = ["PrivatizeReport", "run_privatize"]

_SKIP_REASONS: dict[str, str] = {
    BELOW_FLOOR: "heuristic-confidence",
    "dunder-or-main": "dunder-or-main",
    "already-private": "already-private",
}
"""Mutation refusal reason -> the frozen ``privatize`` report's skip code.

:data:`~pypeeker.dsl.DEMOTE`'s floor is the frozen ``skip_heuristic``
pre-filter branch and its two preconditions are the other two pointwise
branches, so the mapping is one-to-one by construction rather than by
coincidence.

The two precondition rows are unreachable for all three demotion rules today:
``pypeeker.dsl.visibility._candidate_clauses`` already filters ``main`` and
dunders out of the *selection*, and restricts it to public symbols, so no row
reaching ``DEMOTE`` can fail either guard. They are mapped anyway — the
mapping is the contract between two vocabularies, and a rule that widened its
selection must not silently lose a report row. An unmapped reason raises.
"""

_SKIP_DETAILS: dict[str, str] = {
    "heuristic-confidence": (
        "the nominating finding has heuristic confidence "
        "(dynamic access nearby); excluded from auto-fix"
    ),
    "dunder-or-main": "'{name}' has conventional meaning; never demoted",
    "already-private": "'{name}' already starts with an underscore",
}
"""The frozen pre-filter's detail wording, copied so the JSON is unchanged.

Source: ``refactor/privatize.py``'s ``heuristic-confidence``,
``dunder-or-main`` and ``already-private`` branches.
"""


@dataclass(frozen=True)
class PrivatizeReport:
    """The result of :func:`run_privatize`.

    One field, on purpose. The frozen report also carried ``applied`` and
    ``apply_error``, which only ever had values under the dead ``apply_plan``
    parameter; a bare :class:`~pypeeker.refactor.PrivatizeOutcome` would have
    been the honest return type, but keeping the wrapper keeps
    ``report.outcome`` as the CLI's accessor, so B2 changes one import and
    nothing else.
    """

    outcome: PrivatizeOutcome


def _skip_row(decision: MutationDecision) -> SkippedSymbol:
    """The frozen ``skipped`` entry for a row the demote mutation refused.

    Raises:
        ValueError: when ``decision.reason`` is not one the frozen report has
            a code for. Dropping such a row would silently shrink a report
            that exists to explain why a symbol was left alone.
    """
    code = _SKIP_REASONS.get(decision.reason)
    if code is None:  # pragma: no cover - defensive; see _SKIP_REASONS
        raise ValueError(
            f"demote refused '{decision.match.anchor.id}' with reason "
            f"'{decision.reason}', which the privatize report has no skip "
            f"code for (known: {', '.join(sorted(_SKIP_REASONS))})"
        )
    detail = _SKIP_DETAILS[code].format(name=decision.match.fields.get("name"))
    return SkippedSymbol(decision.match.anchor.id, code, detail)


def _prefilter_positions(
    intents: list[ChangeVisibilityIntent], skipped: list[SkippedSymbol]
) -> dict[int, SkippedSymbol]:
    """Which submitted intent each pre-filter skip belongs to, by position.

    ``plan_privatize`` reports its skips in submission order but
    cannot say *which* submission each one came from, and the report the CLI
    renders interleaves both kinds of skip in submitted order. The assignment
    is nevertheless exact: for a symbol id submitted ``n`` times the pre-filter
    yields either ``n`` skips (an id-level refusal, identical for every
    occurrence) or exactly ``n - 1`` (the first occurrence becomes the
    candidate and every later one collides with it in ``pending``). Both cases
    are "the last ``k`` occurrences", so assigning each id's skips to its last
    ``k`` positions reconstructs the interleaving with no guessing.
    """
    positions: dict[str, list[int]] = {}
    for index, intent in enumerate(intents):
        positions.setdefault(intent.symbol_id, []).append(index)
    grouped: dict[str, list[SkippedSymbol]] = {}
    for skip in skipped:
        grouped.setdefault(skip.symbol_id, []).append(skip)
    assigned: dict[int, SkippedSymbol] = {}
    for symbol_id, rows in grouped.items():
        occurrences = positions.get(symbol_id, [])
        if len(occurrences) < len(rows):  # pragma: no cover - defensive
            raise ValueError(
                f"pre-filter reported {len(rows)} skips for '{symbol_id}' but "
                f"only {len(occurrences)} intents were submitted for it"
            )
        for index, skip in zip(occurrences[-len(rows):], rows, strict=True):
            assigned[index] = skip
    return assigned


def run_privatize(
    store: IndexStore,
    transaction_store: TransactionStore,
    root: Path,
    rules: tuple[str, ...] = (),
) -> PrivatizeReport:
    """Plan a mass demotion driven by the DSL demotion rules.

    Runs ``rules`` (or every rule in
    :data:`~pypeeker.dsl.DEMOTION_RULES` when empty) against one shared
    :class:`~pypeeker.dsl.Corpus`, sorts every decision into report order,
    and plans the surviving repairs as one batch through
    :func:`~pypeeker.refactor.plan_privatize`.

    Two details that are easy to get wrong and are load-bearing:

    * The project-wide visibility table is injected under
      :data:`~pypeeker.dsl.PROJECT_VISIBILITY_KEY`, never under
      ``visibility``: the latter is also the name of a rule's *own* enum
      option, and the collision emptied that option in silence (TASK-163).
      The value is the **raw** ``[tool.pypeeker.visibility]`` mapping from
      :func:`~pypeeker.dsl.read_visibility_table`, never a parsed
      :class:`~pypeeker.project.VisibilityConfig`. The rule builders coerce
      the table themselves and refuse a non-``Mapping``, so the frozen
      service's ``base.visibility`` injection would crash on every project
      that declares the section — this repo included.
    * :func:`~pypeeker.dsl.privatize_selections` takes **one** option table
      for all three rules, while the frozen service gave each rule its own
      ``rule_options[name]``. It is called once per rule here, each time with
      that rule's own table, which reproduces the per-rule semantics without
      changing the DSL signature. Building three selections and evaluating
      one is cheap; evaluation is the cost, not construction.

    Decisions are ordered by ``(file_path, line, rule_id)`` before anything is
    submitted. The frozen service consumed ``CheckEngine.run()``'s
    ``(file_path, line, rule, message)`` ordering, while
    ``privatize_selections`` yields rule-major; without the sort, which
    duplicate nomination wins the ``pending-collision`` race would be an
    accident of :data:`~pypeeker.dsl.DEMOTION_RULES` order.
    """
    install_expressions()
    selected = tuple(dict.fromkeys(rules)) or DEMOTION_RULES
    src, _rules, _plugins, options = read_config(root)
    visibility = read_visibility_table(root)

    corpus = Corpus(store, src)
    collected: list[tuple[str, MutationDecision]] = []
    for rule_id in selected:
        per_rule: dict[str, Any] = dict(options.get(rule_id, {}))
        if visibility:
            per_rule[PROJECT_VISIBILITY_KEY] = visibility
        applications = dict(privatize_selections(per_rule))
        application = applications.get(rule_id)
        if application is None:
            raise ValueError(
                f"'{rule_id}' is not a demotion rule; expected one of "
                f"{', '.join(DEMOTION_RULES)}"
            )
        collected.extend(
            (rule_id, decision) for decision in application.decisions(corpus, rule_id)
        )
    collected.sort(
        key=lambda pair: (
            pair[1].match.fields["file_path"],
            pair[1].match.fields["line"],
            pair[0],
        )
    )

    refusals: list[SkippedSymbol | None] = []
    intents: list[ChangeVisibilityIntent] = []
    for rule_id, decision in collected:
        if decision.intent is None:
            refusals.append(_skip_row(decision))
            continue
        if not isinstance(decision.intent, ChangeVisibilityIntent):
            # DEMOTE's declared intent type, checked rather than asserted:
            # plan_privatize reads symbol_id/direction off every entry,
            # and an assert vanishes under -O, which would turn a broken
            # mutation table into an AttributeError deep in the planner.
            raise ValueError(
                f"'{rule_id}' nominated '{decision.match.anchor.id}' with a "
                f"{type(decision.intent).__name__}, but demotion plans only "
                f"{ChangeVisibilityIntent.__name__}"
            )
        intents.append(decision.intent)
        refusals.append(None)

    outcome = plan_privatize(store, transaction_store, intents)
    assigned = _prefilter_positions(intents, outcome.skipped)
    merged: list[SkippedSymbol] = []
    submitted = 0
    for refusal in refusals:
        if refusal is not None:
            merged.append(refusal)
            continue
        skip = assigned.get(submitted)
        if skip is not None:
            merged.append(skip)
        submitted += 1
    outcome.skipped = merged
    return PrivatizeReport(outcome=outcome)
