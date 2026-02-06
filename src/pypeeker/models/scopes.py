"""Scope model."""

from enum import Enum

from pydantic import BaseModel

from .location import Span


class ScopeKind(str, Enum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    COMPREHENSION = "comprehension"
    LAMBDA = "lambda"


class Scope(BaseModel):
    """A container that holds symbols."""

    scope_id: str
    name: str
    kind: ScopeKind
    file_path: str
    span: Span
    parent_scope_id: str | None = None
    child_scope_ids: list[str] = []
    symbol_ids: list[str] = []
