"""Pure utility functions used across the binder visitors.

These functions have no dependency on :class:`BinderState`. They take only
the data they need (a node, a source, a file path) and return a value.
"""

from __future__ import annotations

import builtins
import hashlib

from tree_sitter import Node

from pypeeker.models.location import Location, Position, Span
from pypeeker.models.references import ReferenceKind
from pypeeker.models.scopes import Scope, ScopeKind
from pypeeker.models.symbol_id import builtin_id

BUILTIN_NAMES: frozenset[str] = frozenset(
    name for name in dir(builtins) if not name.startswith("_")
)
"""Names exposed by the ``builtins`` module — functions, types, exceptions,
constants — introspected at module load. Dunders (``__import__``, ``__name__``,
...) are filtered out: they aren't names that appear at call sites in normal
Python code, and treating them as resolved would mask real unresolved refs."""


def builtin_symbol_id(name: str) -> str:
    """Synthetic ``symbol_id`` for a reference resolved to a Python builtin.

    Thin delegation to :func:`pypeeker.models.symbol_id.builtin_id` — the
    grammar is owned by ``pypeeker.models.symbol_id``.
    """
    return builtin_id(name)


def make_span(node: Node) -> Span:
    """Convert a tree-sitter node's start/end points into a ``Span``."""
    return Span(
        start=Position(line=node.start_point[0], column=node.start_point[1]),
        end=Position(line=node.end_point[0], column=node.end_point[1]),
    )


def node_key(node: Node) -> tuple[int, int]:
    """Stable identity for a CST node: its ``(start_byte, end_byte)`` span.

    tree-sitter Node wrappers are ephemeral and ``id(node)`` is reused as they
    are garbage-collected, so it can't be used to remember which nodes have
    been handled. The byte span is unique per node and stable.
    """
    return (node.start_byte, node.end_byte)


def make_location(file_path: str, node: Node) -> Location:
    """Build a ``Location`` for ``node`` within ``file_path``."""
    return Location(file_path=file_path, span=make_span(node))


def compute_hash(source: bytes) -> str:
    """SHA-256 of source bytes — used for stale-index detection."""
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


def build_symbol_id_for_scope(scope: Scope, name: str, id_root: str) -> str:
    """Build a symbol_id for a specific scope (used for global/nonlocal redirects).

    ``id_root`` is the dotted module path; for module scope the id is rooted
    there, otherwise it hangs off the target scope's own id.
    """
    if scope.kind == ScopeKind.MODULE:
        return f"{id_root}:{name}"
    return f"{scope.scope_id}:{name}"


def resolve_relative_import(
    module_path: str, module_name: str, *, is_package: bool = False
) -> str:
    """Resolve a relative import to an absolute module path.

    Resolution happens against ``module_path`` — the dotted *semantic* module
    path of the importing file (src-root already stripped, e.g.
    ``pkg.models.index`` for ``src/pkg/models/index.py``) — so the result
    lives in the same namespace as indexed modules, never the physical file
    tree. Resolving against the file path would leak layout prefixes
    (``src.pkg.models.references``) that no indexed module ever matches.

    Level-1 (``.x``) resolves against the containing package: the parent of a
    regular module, but ``module_path`` itself for a package ``__init__.py``
    (whose module path already names the package, the "directory
    equivalent"). Each extra leading dot climbs one more package.

    Examples:
        module_path="pkg.models.index", ".references"      → "pkg.models.references"
        module_path="pkg.models", ".user" (is_package)     → "pkg.models.user"
        module_path="pkg.sub.mod", "..other"               → "pkg.other"
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

    parts = [p for p in module_path.split(".") if p]
    # A package's module_path already names its "directory", so one fewer
    # segment is stripped than for a regular module file.
    strip = level - 1 if is_package else level
    base_parts = parts[: len(parts) - strip] if strip <= len(parts) else []

    if base_parts:
        base = ".".join(base_parts)
        return f"{base}.{relative_part}" if relative_part else base
    return relative_part if relative_part else ""
