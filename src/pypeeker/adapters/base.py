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
    def language_name(self) -> str:
        """Canonical identifier for the language (e.g. ``"python"``)."""
        ...

    @property
    def capabilities(self) -> dict[Capability, Confidence]:
        """Which semantic capabilities this adapter exposes, and how reliable each is."""
        ...

    def parse(self, source: bytes) -> Tree:
        """Parse source bytes into a tree-sitter ``Tree``."""
        ...

    def is_scope_node(self, node: Node) -> bool:
        """True if ``node`` introduces a new lexical scope (function, class, ...)."""
        ...

    def is_declaration_node(self, node: Node) -> bool:
        """True if ``node`` declares a name (function/class/import/assignment target)."""
        ...

    def is_reference_node(self, node: Node) -> bool:
        """True if ``node`` is a use site (identifier read, call, attribute access)."""
        ...

    def extract_name(self, node: Node) -> str | None:
        """Return the bound name for a declaration node, or ``None`` if not applicable."""
        ...

    def get_visibility(self, name: str) -> tuple[Visibility, Confidence]:
        """Classify a name as public / protected / private / dunder by convention."""
        ...

    def get_type_annotation(self, node: Node) -> tuple[str | None, Confidence]:
        """Return the raw type annotation text attached to ``node``, if any."""
        ...
