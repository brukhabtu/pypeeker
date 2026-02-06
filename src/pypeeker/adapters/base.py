"""Language adapter protocol."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from pypeeker.models.capabilities import Capability, Confidence
from pypeeker.models.symbols import Visibility

if TYPE_CHECKING:
    from tree_sitter import Node, Tree


class LanguageAdapter(Protocol):
    """Protocol that all language adapters implement."""

    @property
    def language_name(self) -> str: ...

    @property
    def capabilities(self) -> dict[Capability, Confidence]: ...

    def parse(self, source: bytes) -> Tree: ...

    def is_scope_node(self, node: Node) -> bool: ...

    def is_declaration_node(self, node: Node) -> bool: ...

    def is_reference_node(self, node: Node) -> bool: ...

    def extract_name(self, node: Node) -> str | None: ...

    def get_visibility(self, name: str) -> tuple[Visibility, Confidence]: ...

    def get_type_annotation(self, node: Node) -> tuple[str | None, Confidence]: ...
