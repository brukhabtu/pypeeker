"""Per-file index model."""

from pydantic import BaseModel

from .references import Reference
from .scopes import Scope
from .symbols import Symbol


class FileIndex(BaseModel):
    """The per-file JSON index stored in .semantic-tool/index/."""

    file_path: str
    file_hash: str
    language: str
    symbols: list[Symbol]
    scopes: list[Scope]
    references: list[Reference]
    errors: list[str] = []
