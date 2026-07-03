"""Violation model for the check command."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pypeeker.models import Confidence

if TYPE_CHECKING:
    from pypeeker.check.fixes import Fix


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
    (``--strict`` shows them); fix application can gate on it later.

    ``fix`` optionally carries a :class:`pypeeker.check.fixes.Fix` planner so
    ``check --fix`` (and intent wrappers) can repair what the rule flagged.
    It is deliberately ``compare=False`` so attaching a fix changes nothing
    about equality, ordering, or hashing — violations with and without fixes
    sort identically — and ``repr=False`` so output is unchanged. Attach it
    with :func:`pypeeker.check.fixes.with_fix`, not by hand.

    Violations are in-memory only: nothing serializes or persists them (the
    engine returns them, the CLI prints ``str(v)``), so the ``fix`` object
    reference never needs to round-trip through JSON.
    """

    file_path: str
    line: int
    rule: str
    message: str
    confidence: Confidence = field(default=Confidence.DECLARED, compare=False)
    fix: Fix | None = field(default=None, compare=False, repr=False)

    def __str__(self) -> str:
        marker = (
            "" if self.confidence is Confidence.DECLARED
            else f" [{self.confidence.value}]"
        )
        return f"{self.file_path}:{self.line}: [{self.rule}] {self.message}{marker}"
