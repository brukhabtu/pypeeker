"""Violation model for the check command."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class Violation:
    """One rule firing on one location.

    Field order matters: ``order=True`` gives us deterministic sorting by
    (file_path, line, rule, message) which is what the engine relies on.
    """

    file_path: str
    line: int
    rule: str
    message: str

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.rule}] {self.message}"
