"""Symbol model."""

from enum import Enum

from pydantic import BaseModel

from .capabilities import Confidence
from .location import Location


class SymbolKind(str, Enum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    VARIABLE = "variable"
    PARAMETER = "parameter"
    IMPORT = "import"
    PROPERTY = "property"
    DECORATOR = "decorator"


class Visibility(str, Enum):
    PUBLIC = "public"
    PROTECTED = "protected"  # Single leading underscore
    PRIVATE = "private"  # Double leading underscore (name-mangled)
    DUNDER = "dunder"  # Double leading + trailing underscore


class TypeAnnotation(BaseModel):
    """Optional type info attached to a symbol."""

    raw: str
    confidence: Confidence


class Symbol(BaseModel):
    """A named entity in source code."""

    symbol_id: str
    name: str
    kind: SymbolKind
    location: Location
    visibility: Visibility
    visibility_confidence: Confidence
    type_annotation: TypeAnnotation | None = None
    decorators: list[str] = []
    docstring: str | None = None
    parent_scope_id: str | None = None
