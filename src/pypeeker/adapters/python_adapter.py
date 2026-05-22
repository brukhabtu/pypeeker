"""Python language adapter using tree-sitter."""

from __future__ import annotations

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser, Tree

from pypeeker.models.capabilities import Capability, Confidence
from pypeeker.models.symbols import Visibility

PY_LANGUAGE = Language(tspython.language())

_SCOPE_TYPES = frozenset({
    "module",
    "function_definition",
    "class_definition",
    "lambda",
    "list_comprehension",
    "set_comprehension",
    "dictionary_comprehension",
    "generator_expression",
})

_DECLARATION_TYPES = frozenset({
    "function_definition",
    "class_definition",
    "assignment",
    "augmented_assignment",
    "named_expression",
    "for_statement",
    "with_statement",
    "except_clause",
    "import_statement",
    "import_from_statement",
    "global_statement",
    "nonlocal_statement",
    "decorated_definition",
})


class PythonAdapter:
    """Python language adapter backed by tree-sitter-python."""

    def __init__(self) -> None:
        self._parser = Parser(PY_LANGUAGE)

    @property
    def language_name(self) -> str:
        """Canonical identifier for the language."""
        return "python"

    @property
    def capabilities(self) -> dict[Capability, Confidence]:
        """Per-capability confidence levels for Python."""
        return {
            Capability.VISIBILITY: Confidence.HEURISTIC,
            Capability.STATIC_TYPES: Confidence.DECLARED,
            Capability.IMPORT_RESOLUTION: Confidence.DECLARED,
        }

    def parse(self, source: bytes) -> Tree:
        """Parse source bytes into a tree-sitter ``Tree``."""
        tree = self._parser.parse(source)
        if tree is None:
            raise ValueError("Failed to parse source")
        return tree

    def is_scope_node(self, node: Node) -> bool:
        """True for module / def / class / lambda / comprehension nodes."""
        return node.type in _SCOPE_TYPES

    def is_declaration_node(self, node: Node) -> bool:
        """True for statements that introduce a new name binding."""
        return node.type in _DECLARATION_TYPES

    def is_reference_node(self, node: Node) -> bool:
        """True for identifier nodes — every name-use site."""
        return node.type == "identifier"

    def extract_name(self, node: Node) -> str | None:
        """Return the declared name for a def / class / identifier node."""
        if node.type in ("function_definition", "class_definition"):
            name_node = node.child_by_field_name("name")
            return name_node.text.decode("utf-8") if name_node else None
        if node.type == "identifier":
            return node.text.decode("utf-8")
        return None

    def get_visibility(self, name: str) -> tuple[Visibility, Confidence]:
        """Classify a name by Python's underscore conventions."""
        if name.startswith("__") and name.endswith("__") and len(name) > 4:
            return Visibility.DUNDER, Confidence.HEURISTIC
        if name.startswith("__"):
            return Visibility.PRIVATE, Confidence.HEURISTIC
        if name.startswith("_"):
            return Visibility.PROTECTED, Confidence.HEURISTIC
        return Visibility.PUBLIC, Confidence.HEURISTIC

    def get_type_annotation(self, node: Node) -> tuple[str | None, Confidence]:
        """Return the raw annotation text from a node's ``type`` field, if any."""
        type_node = node.child_by_field_name("type")
        if type_node:
            return type_node.text.decode("utf-8"), Confidence.DECLARED
        return None, Confidence.UNKNOWN
