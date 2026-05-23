"""Symbol model."""

from dataclasses import dataclass, field
from enum import Enum

from .capabilities import Confidence
from .location import Location


class SymbolKind(str, Enum):
    """What kind of named entity a Symbol represents."""

    PACKAGE = "package"
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
    """Public / protected / private / dunder, classified by Python's underscore conventions."""

    PUBLIC = "public"
    PROTECTED = "protected"  # Single leading underscore
    PRIVATE = "private"  # Double leading underscore (name-mangled)
    DUNDER = "dunder"  # Double leading + trailing underscore


@dataclass
class TypeAnnotation:
    """Optional type info attached to a symbol."""

    raw: str
    confidence: Confidence


@dataclass
class Symbol:
    """A named entity in source code."""

    symbol_id: str
    name: str
    kind: SymbolKind
    location: Location
    visibility: Visibility
    visibility_confidence: Confidence
    type_annotation: TypeAnnotation | None = None
    decorators: list[str] = field(default_factory=list)
    docstring: str | None = None
    parent_scope_id: str | None = None
    # Import tracking fields
    imported_from: str | None = None  # e.g., "lib.helper" for "from lib import helper"
    imported_name_location: Location | None = None  # For aliased imports: location of original name
