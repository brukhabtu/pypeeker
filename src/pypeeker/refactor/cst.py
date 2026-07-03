"""CST (tree-sitter) utilities for refactoring.

The semantic index throws away statement structure, so structural refactors
work on the concrete syntax tree instead. These helpers are transient — the
tree is re-parsed per refactor and never persisted — and turn tree-sitter nodes
into byte-precise :class:`EditEntry` values, so edits change exactly the
selected bytes and preserve all surrounding formatting.

These helpers are Python-CST-specific (they match tree-sitter-python node
types) and form the CST-editing third of the Python language adapter
boundary {``adapters.python_adapter`` + ``binder`` + ``refactor.cst``};
see ``pypeeker.adapters``.
"""

from __future__ import annotations

from tree_sitter import Node

from pypeeker.adapters.python_adapter import PythonAdapter
from pypeeker.models import EditEntry, EditOp

# Node types that sit directly inside a block/module body — i.e. statements.
_STATEMENT_PARENTS = ("block", "module")


def parse(source: bytes) -> Node:
    """Parse source bytes and return the CST root node."""
    return PythonAdapter().parse(source).root_node


def expression_at(root: Node, line: int, column: int) -> Node | None:
    """Smallest named node at a 0-indexed ``(line, column)`` position."""
    return root.named_descendant_for_point_range((line, column), (line, column))


def node_spanning(root: Node, start: tuple[int, int], end: tuple[int, int]) -> Node | None:
    """Smallest named node covering the ``(line, col)`` range ``[start, end]``."""
    return root.named_descendant_for_point_range(start, end)


def enclosing_statement(node: Node) -> Node | None:
    """The statement node containing ``node`` (a direct child of a block/module)."""
    current: Node | None = node
    while current is not None and current.parent is not None:
        if current.parent.type in _STATEMENT_PARENTS:
            return current
        current = current.parent
    return None


def node_text(node: Node, source: bytes) -> str:
    """The source text of ``node``."""
    return source[node.start_byte : node.end_byte].decode("utf-8")


def line_start_byte(node: Node) -> int:
    """Byte offset of the start of the line ``node`` begins on."""
    return node.start_byte - node.start_point[1]


def indent_of(node: Node, source: bytes) -> str:
    """Leading whitespace of the line ``node`` begins on."""
    return source[line_start_byte(node) : node.start_byte].decode("utf-8")


def replace_edit(
    file: str, node: Node, new: str, file_hash: str, source: bytes
) -> EditEntry:
    """A REPLACE edit covering ``node``'s byte span."""
    return EditEntry(
        file=file,
        start=node.start_byte,
        end=node.end_byte,
        old=node_text(node, source),
        new=new,
        file_hash=file_hash,
        op=EditOp.REPLACE,
    )


def insert_edit(file: str, byte_offset: int, text: str, file_hash: str) -> EditEntry:
    """An INSERT edit placing ``text`` at ``byte_offset`` (zero-width)."""
    return EditEntry(
        file=file,
        start=byte_offset,
        end=byte_offset,
        old="",
        new=text,
        file_hash=file_hash,
        op=EditOp.INSERT,
    )
