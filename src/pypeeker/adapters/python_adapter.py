"""Python language adapter using tree-sitter.

This module is one third of the Python "adapter". The full adapter is the
package boundary {``adapters.python_adapter`` + ``binder`` + ``refactor.cst``}:
this module supplies parsing and visibility conventions, ``pypeeker.binder``
turns the CST into the language-agnostic ``FileIndex``, and
``pypeeker.refactor.cst`` provides Python-CST edit helpers. See
``pypeeker.adapters.base`` for the contract.
"""

from __future__ import annotations

import tree_sitter_python as tspython
from tree_sitter import Language, Parser, Tree

from pypeeker.models.capabilities import Confidence
from pypeeker.models.symbols import Visibility

PY_LANGUAGE = Language(tspython.language())


class PythonAdapter:
    """Python language adapter backed by tree-sitter-python."""

    def __init__(self) -> None:
        self._parser = Parser(PY_LANGUAGE)

    @property
    def language_name(self) -> str:
        """Canonical identifier for the language."""
        return "python"

    def parse(self, source: bytes) -> Tree:
        """Parse source bytes into a tree-sitter ``Tree``."""
        tree = self._parser.parse(source)
        if tree is None:
            raise ValueError("Failed to parse source")
        return tree

    def get_visibility(self, name: str) -> tuple[Visibility, Confidence]:
        """Classify a name by Python's underscore conventions."""
        if name.startswith("__") and name.endswith("__") and len(name) > 4:
            return Visibility.DUNDER, Confidence.HEURISTIC
        if name.startswith("__"):
            return Visibility.PRIVATE, Confidence.HEURISTIC
        if name.startswith("_"):
            return Visibility.PROTECTED, Confidence.HEURISTIC
        return Visibility.PUBLIC, Confidence.HEURISTIC
