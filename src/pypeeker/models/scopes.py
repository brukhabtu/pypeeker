"""Scope model."""

from dataclasses import dataclass, field
from enum import Enum

from .location import Span


class ScopeKind(str, Enum):
    """Lexical container types: module, class, function, comprehension, lambda,
    type_params."""

    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    COMPREHENSION = "comprehension"
    LAMBDA = "lambda"
    TYPE_PARAMS = "type_params"
    """Holds the type parameters of a ``type X[T] = ...`` statement, which has
    no scope of its own to host them. Functions and classes host their own PEP
    695 type parameters directly in their existing FUNCTION/CLASS scope
    instead of a scope of this kind. The alias's value, like any generic
    definition's header, is bound in the *enclosing* scope with the type
    parameters overlaid — see architecture.md."""


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
