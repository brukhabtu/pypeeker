"""Reference model."""

from dataclasses import dataclass
from enum import Enum

from .location import Location


class ReferenceKind(str, Enum):
    """How a name is being used at a reference site."""

    READ = "read"
    WRITE = "write"
    CALL = "call"
    IMPORT = "import"
    TYPE_ANNOTATION = "type_annotation"
    DECORATOR = "decorator"
    DEFINITION = "definition"


@dataclass
class Reference:
    """A usage of a symbol."""

    symbol_id: str
    kind: ReferenceKind
    location: Location
    in_scope_id: str
    resolved: bool = True
    is_attribute_access: bool = False
    receiver_root_symbol_id: str | None = None
    """For attribute access (``a.b.c``), the resolved symbol_id of the leftmost
    name in the receiver chain. None when the chain is dynamic
    (``f().bar``, ``lst[0].bar``) or the leftmost name doesn't resolve."""
    receiver_chain: list[str] | None = None
    """The receiver names from root to second-to-last, inclusive of the root.
    For ``os.path.join(x)`` this is ``['os', 'path']``. For ``path.write_text(x)``
    it is ``['path']``. None when the chain is dynamic."""
