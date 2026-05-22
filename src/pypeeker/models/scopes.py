"""Scope model."""

from dataclasses import dataclass, field
from enum import Enum

from .location import Span


class ScopeKind(str, Enum):
    """Lexical container types: module, class, function, comprehension, lambda."""

    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    COMPREHENSION = "comprehension"
    LAMBDA = "lambda"


@dataclass
class Scope:
    """A container that holds symbols."""

    scope_id: str
    name: str
    kind: ScopeKind
    file_path: str
    span: Span
    parent_scope_id: str | None = None
    child_scope_ids: list[str] = field(default_factory=list)
    symbol_ids: list[str] = field(default_factory=list)
