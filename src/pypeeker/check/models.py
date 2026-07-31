"""Violation model for the check command, and the remedy-attachment idiom."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

from pypeeker.intents import Intent
from pypeeker.models import Confidence


@dataclass(frozen=True, order=True)
class Violation:
    """One rule firing on one location.

    Field order matters: ``order=True`` gives us deterministic sorting by
    (file_path, line, rule, message) which is what the engine relies on.

    ``confidence`` labels how the finding was resolved, reusing the project's
    :class:`~pypeeker.models.capabilities.Confidence` tiers: ``DECLARED``
    (the default — the rule read a declared fact directly), ``INFERRED``,
    ``HEURISTIC`` (e.g. name matching on an unresolved receiver, or a subject
    that dynamic access could reach invisibly), and ``UNKNOWN``. It is
    ``compare=False`` so equality/ordering/hashing are byte-identical to the
    pre-confidence semantics; ``__str__`` appends a ``[tier]`` marker only
    for non-``DECLARED`` tiers, so output for certain findings is unchanged.
    The check CLI hides ``HEURISTIC``/``UNKNOWN`` findings by default
    (``--strict`` shows them); fix application gates on it.

    ``remedy`` optionally carries the :class:`~pypeeker.intents.Intent` that
    repairs what the rule flagged — *what* should change, never *how*: the
    matching planner in ``refactor`` (reached through the planner registry,
    never imported here) turns it into edits at apply time. ``check --fix``
    and ``plan-batch``'s ``fix`` sweep both submit these intents through the
    one execution pipeline (:mod:`pypeeker.app.submit`). It is deliberately
    ``compare=False`` so attaching a remedy changes nothing about equality,
    ordering, or hashing — violations with and without remedies sort
    identically — and ``repr=False`` so output is unchanged. Attach it with
    :func:`with_remedy`, not by hand.

    Violations are in-memory only: nothing serializes or persists them (the
    engine returns them, the CLI prints ``str(v)``), so the ``remedy`` object
    reference never needs to round-trip through JSON.
    """

    file_path: str
    line: int
    rule: str
    message: str
    confidence: Confidence = field(default=Confidence.DECLARED, compare=False)
    remedy: Intent | None = field(default=None, compare=False, repr=False)

    def __str__(self) -> str:
        marker = (
            "" if self.confidence is Confidence.DECLARED
            else f" [{self.confidence.value}]"
        )
        return f"{self.file_path}:{self.line}: [{self.rule}] {self.message}{marker}"


def with_remedy(violation: Violation, remedy: Intent) -> Violation:
    """THE way rules attach a repair intent to a violation.

    Returns a copy of ``violation`` carrying ``remedy``; the original is
    untouched (Violation is frozen). Because ``Violation.remedy`` is excluded
    from comparison and repr, the returned violation sorts, compares, and
    prints exactly like the original.

    By convention the intent's ``intent_id`` is the rule's stable repair id
    (``"<rule>:<operation>:<anchor>"``) — it is what ``check --fix`` reports
    as ``fix_id``, so it must stay stable across runs for the same logical
    repair.
    """
    return dataclasses.replace(violation, remedy=remedy)
