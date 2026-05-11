"""Per-file index model."""

from dataclasses import dataclass, field

from .references import Reference
from .scopes import Scope
from .symbols import Symbol


@dataclass
class FileIndex:
    """The per-file JSON index stored in .semantic-tool/index/."""

    file_path: str
    file_hash: str
    language: str
    symbols: list[Symbol] = field(default_factory=list)
    scopes: list[Scope] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
