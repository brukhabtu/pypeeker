"""Pure utility functions used across the binder visitors.

These functions have no dependency on :class:`BinderState`. They take only
the data they need (a node, a source, a file path) and return a value.
"""

from __future__ import annotations

import builtins
import hashlib
from pathlib import Path

from tree_sitter import Node

from pypeeker.models.location import Location, Position, Span
from pypeeker.models.references import ReferenceKind
from pypeeker.models.scopes import Scope, ScopeKind

BUILTIN_NAMES: frozenset[str] = frozenset(
    name for name in dir(builtins) if not name.startswith("_")
)
"""Names exposed by the ``builtins`` module — functions, types, exceptions,
constants — introspected at module load. Dunders (``__import__``, ``__name__``,
...) are filtered out: they aren't names that appear at call sites in normal
Python code, and treating them as resolved would mask real unresolved refs."""


def builtin_symbol_id(name: str) -> str:
    """Synthetic ``symbol_id`` for a reference resolved to a Python builtin."""
    return f"<builtins>.{name}"


def make_span(node: Node) -> Span:
    return Span(
        start=Position(line=node.start_point[0], column=node.start_point[1]),
        end=Position(line=node.end_point[0], column=node.end_point[1]),
    )


def make_location(file_path: str, node: Node) -> Location:
    return Location(file_path=file_path, span=make_span(node))


def compute_hash(source: bytes) -> str:
    return hashlib.sha256(source).hexdigest()


def extract_targets(node: Node) -> list[Node]:
    """Extract assignment targets, handling tuple unpacking.

    Only extracts identifier targets. Attribute and subscript targets are
    skipped — they create references, not declarations.
    """
    targets: list[Node] = []
    if node.type == "identifier":
        targets.append(node)
    elif node.type in ("pattern_list", "tuple_pattern", "list_pattern"):
        for child in node.children:
            targets.extend(extract_targets(child))
    elif node.type in ("tuple", "list"):
        for child in node.children:
            targets.extend(extract_targets(child))
    elif node.type == "list_splat_pattern":
        for child in node.children:
            if child.type == "identifier":
                targets.append(child)
    return targets


def extract_docstring(node: Node) -> str | None:
    """Extract docstring from a function or class definition."""
    body = node.child_by_field_name("body")
    if not body or not body.children:
        return None
    first = body.children[0]
    if first.type == "expression_statement" and first.children:
        string_node = first.children[0]
        if string_node.type == "string":
            text = string_node.text.decode("utf-8")
            if text.startswith('"""') or text.startswith("'''"):
                return text[3:-3].strip()
            if text.startswith('"') or text.startswith("'"):
                return text[1:-1].strip()
    return None


def determine_reference_kind(node: Node) -> ReferenceKind:
    """Classify a non-attribute identifier reference based on its parent."""
    parent = node.parent
    if parent is None:
        return ReferenceKind.READ
    if parent.type == "decorator":
        return ReferenceKind.DECORATOR
    if parent.type == "type":
        return ReferenceKind.TYPE_ANNOTATION
    if parent.type == "call" and node == parent.child_by_field_name("function"):
        return ReferenceKind.CALL
    return ReferenceKind.READ


def determine_attribute_ref_kind(node: Node) -> ReferenceKind:
    """Classify an attribute-access reference as READ or WRITE based on context."""
    parent = node.parent
    if parent is None:
        return ReferenceKind.READ
    if parent.type == "assignment":
        if parent.child_by_field_name("left") == node:
            return ReferenceKind.WRITE
    if parent.type == "augmented_assignment":
        if parent.child_by_field_name("left") == node:
            return ReferenceKind.WRITE
    return ReferenceKind.READ


def build_symbol_id_for_scope(scope: Scope, name: str, file_path: str) -> str:
    """Build a symbol_id for a specific scope (used for global/nonlocal redirects)."""
    if scope.kind == ScopeKind.MODULE:
        return f"{file_path}:{name}"
    return f"{scope.scope_id}:{name}"


def resolve_relative_import(file_path: str, module_name: str) -> str:
    """Resolve a relative import to an absolute module path.

    For ``models/__init__.py`` with ``from .user import X``:
        module_name=".user" → "models.user"
    For ``pkg/sub/mod.py`` with ``from ..other import X``:
        module_name="..other" → "pkg.other"
    """
    if not module_name.startswith("."):
        return module_name

    level = 0
    for char in module_name:
        if char == ".":
            level += 1
        else:
            break

    relative_part = module_name[level:]

    fp = Path(file_path)
    package_parts = list(fp.parent.parts)

    if level > 1:
        package_parts = (
            package_parts[: -(level - 1)]
            if level - 1 < len(package_parts)
            else []
        )

    if package_parts:
        base = ".".join(package_parts)
        return f"{base}.{relative_part}" if relative_part else base
    return relative_part if relative_part else ""
