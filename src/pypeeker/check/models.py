"""Violation model for the check command."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pypeeker.check.fixes import Fix


@dataclass(frozen=True, order=True)
class Violation:
    """One rule firing on one location.

    Field order matters: ``order=True`` gives us deterministic sorting by
    (file_path, line, rule, message) which is what the engine relies on.

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
    fix: Fix | None = field(default=None, compare=False, repr=False)

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.rule}] {self.message}"
