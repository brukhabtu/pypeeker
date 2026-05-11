"""Violation model for the check command."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class Violation:
    file_path: str
    line: int
    rule: str
    message: str

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.rule}] {self.message}"
