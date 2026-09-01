"""Replace-text preconditions (:mod:`pypeeker.refactor.text_ops`, TASK-125)."""

from __future__ import annotations

from typing import ClassVar

from pypeeker.refactor.preconditions.base import (
    _PASS,
    Precondition,
    PreconditionResult,
    _fail,
)


# ---------------------------------------------------------------------------
# Replace-text (TASK-125)
# ---------------------------------------------------------------------------


class OccurrenceExists(Precondition):
    """The expected text occurs at least once in the current file (slug ``"text-mismatch"``)."""

    name = "occurrence-exists"
    slug: ClassVar[str] = "text-mismatch"

    def __init__(self, content: bytes, needle: bytes, old_text: str, file_path: str) -> None:
        self.content = content
        self.needle = needle
        self.old_text = old_text
        self.file_path = file_path
        self.first: int = -1

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        first = self.content.find(self.needle)
        if first == -1:
            return _fail(f"expected text {self.old_text!r} not found in {self.file_path}")
        self.first = first
        return _PASS


class UniqueOccurrence(Precondition):
    """The expected text occurs exactly once, so re-anchoring is unambiguous (slug ``"ambiguous"``)."""

    name = "unique-occurrence"
    slug: ClassVar[str] = "ambiguous"

    def __init__(
        self, content: bytes, needle: bytes, first: int, old_text: str, file_path: str
    ) -> None:
        self.content = content
        self.needle = needle
        self.first = first
        self.old_text = old_text
        self.file_path = file_path

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.content.find(self.needle, self.first + 1) != -1:
            return _fail(
                f"expected text {self.old_text!r} occurs more than once in {self.file_path}"
            )
        return _PASS
