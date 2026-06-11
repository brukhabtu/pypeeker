"""Language adapter protocol.

``LanguageAdapter`` is deliberately small: it covers only what consumers
actually call today — parsing source to a tree-sitter CST, naming the
language, and classifying name visibility. The real language-agnostic seam
in this codebase is :class:`~pypeeker.models.index.FileIndex`: everything
downstream of the binder (storage, query, analysis, check, refactor
planning) consumes ``FileIndex`` and never touches language-specific code.

In practice the "Python adapter" is a package boundary, not a single class:

- ``pypeeker.adapters.python_adapter`` — parsing + visibility conventions
- ``pypeeker.binder`` — walks the Python CST into ``FileIndex``
  (hardcodes tree-sitter-python node types by design)
- ``pypeeker.refactor.cst`` — Python-CST edit helpers for refactors

A second language would supply equivalents of all three, producing the same
``FileIndex`` shape; it would not merely implement this protocol.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from pypeeker.models.capabilities import Confidence
from pypeeker.models.symbols import Visibility

if TYPE_CHECKING:
    from tree_sitter import Tree


class LanguageAdapter(Protocol):
    """Protocol covering the adapter surface consumers actually use."""

    @property
    def language_name(self) -> str:
        """Canonical identifier for the language (e.g. ``"python"``)."""
        ...

    def parse(self, source: bytes) -> Tree:
        """Parse source bytes into a tree-sitter ``Tree``."""
        ...

    def get_visibility(self, name: str) -> tuple[Visibility, Confidence]:
        """Classify a name as public / protected / private / dunder by convention."""
        ...
