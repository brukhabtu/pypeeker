"""Source location models."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Position:
    """A line:column position in a source file. Both are 0-indexed."""

    line: int
    column: int


@dataclass(frozen=True)
class Span:
    """A range in a source file."""

    start: Position
    end: Position


@dataclass(frozen=True)
class Location:
    """A span within a specific file."""

    file_path: str
    span: Span
