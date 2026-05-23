"""Cross-file symbol-tree model.

The per-file :class:`pypeeker.models.index.FileIndex` already captures the
structure *inside* a module (class/function symbols chained by
``parent_scope_id`` up to the module scope). This module adds the skeleton
*above* the module boundary: the package -> subpackage -> module spine, keyed
by dotted symbol id, persisted as its own artifact.

Each node carries a ``subtree_hash`` derived bottom-up from its own source hash
and its children's subtree hashes. The hash is membership-aware: adding,
removing, renaming, or editing a member changes the hash of exactly that node
and its ancestors, leaving sibling subtrees byte-identical. That property is
what lets reads rebuild only the affected subtrees.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .symbols import SymbolKind


@dataclass
class TreeNode:
    """A package or module in the cross-file symbol tree.

    ``symbol_id`` is the dotted path (``pypeeker.analysis.calls``), matching the
    module scope id used by the binder. ``file_path``/``file_hash`` are set when
    the node is backed by a real source file (a module, or a package with an
    ``__init__.py``); a namespace package (directory with no ``__init__``) has
    children but no file.
    """

    symbol_id: str
    name: str
    kind: SymbolKind
    subtree_hash: str = ""
    parent_id: str | None = None
    file_path: str | None = None
    file_hash: str | None = None
    children: list[str] = field(default_factory=list)


@dataclass
class TreeIndex:
    """The persisted package/module skeleton, keyed by dotted symbol id."""

    nodes: dict[str, TreeNode] = field(default_factory=dict)
    root_ids: list[str] = field(default_factory=list)
