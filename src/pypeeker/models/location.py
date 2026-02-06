"""Source location models."""

from pydantic import BaseModel


class Position(BaseModel):
    """A line:column position in a source file. Both are 0-indexed."""

    line: int
    column: int


class Span(BaseModel):
    """A range in a source file."""

    start: Position
    end: Position


class Location(BaseModel):
    """A span within a specific file."""

    file_path: str
    span: Span
