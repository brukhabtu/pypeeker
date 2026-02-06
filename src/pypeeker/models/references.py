"""Reference model."""

from enum import Enum

from pydantic import BaseModel

from .location import Location


class ReferenceKind(str, Enum):
    READ = "read"
    WRITE = "write"
    CALL = "call"
    IMPORT = "import"
    TYPE_ANNOTATION = "type_annotation"
    DECORATOR = "decorator"
    DEFINITION = "definition"


class Reference(BaseModel):
    """A usage of a symbol."""

    symbol_id: str
    kind: ReferenceKind
    location: Location
    in_scope_id: str
    resolved: bool = True
    is_attribute_access: bool = False
