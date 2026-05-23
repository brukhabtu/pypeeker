"""Build and incrementally refresh the cross-file symbol tree.

:func:`build_tree` assembles a :class:`~pypeeker.models.tree.TreeIndex` from the
indexed modules. :func:`load_or_rebuild` keeps a persisted tree in sync with the
per-file indexes, reusing subtrees whose membership-aware ``subtree_hash`` is
unchanged and only reconstructing the parts touched by added, removed, or edited
modules.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from pypeeker.paths import module_path_from
from pypeeker.models.index import FileIndex
from pypeeker.models.symbols import SymbolKind
from pypeeker.models.tree import TreeIndex, TreeNode
from pypeeker.storage.index_store import IndexStore
from pypeeker.storage.tree_store import TreeStore


@dataclass(frozen=True)
class _ModuleSrc:
    module_path: str
    file_path: str
    file_hash: str


@dataclass
class RebuildResult:
    """Outcome of :func:`load_or_rebuild`.

    ``rebuilt`` / ``removed`` name the node ids whose subtree changed since the
    cached tree; ``reused`` names the unchanged ones. An empty ``rebuilt`` and
    ``removed`` means the read hit the no-change fast path.
    """

    tree: TreeIndex
    rebuilt: set[str] = field(default_factory=set)
    reused: set[str] = field(default_factory=set)
    removed: set[str] = field(default_factory=set)


def _module_src(index: FileIndex) -> _ModuleSrc | None:
    """Derive (module_path, file_path, file_hash) from a FileIndex."""
    for symbol in index.symbols:
        if symbol.kind == SymbolKind.MODULE:
            return _ModuleSrc(symbol.symbol_id, index.file_path, index.file_hash)
    # Fallback for indexes produced before module symbols existed.
    module_path = module_path_from(index.file_path)
    if not module_path:
        return None
    return _ModuleSrc(module_path, index.file_path, index.file_hash)


def build_tree(indexes: list[FileIndex]) -> TreeIndex:
    """Assemble the package/module skeleton from indexed modules."""
    nodes: dict[str, TreeNode] = {}

    srcs: list[_ModuleSrc] = []
    for index in indexes:
        src = _module_src(index)
        if src is not None:
            srcs.append(src)

    for src in srcs:
        node = nodes.get(src.module_path)
        if node is None:
            node = TreeNode(
                symbol_id=src.module_path,
                name=src.module_path.rsplit(".", 1)[-1],
                kind=SymbolKind.MODULE,
            )
            nodes[src.module_path] = node
        node.file_path = src.file_path
        node.file_hash = src.file_hash

    for src in srcs:
        _ensure_ancestors(nodes, src.module_path)

    for node in nodes.values():
        if node.children:
            node.kind = SymbolKind.PACKAGE

    root_ids = sorted(nid for nid, n in nodes.items() if n.parent_id is None)
    for rid in root_ids:
        _compute_subtree_hash(nodes, rid)

    return TreeIndex(nodes=nodes, root_ids=root_ids)


def _ensure_ancestors(nodes: dict[str, TreeNode], module_path: str) -> None:
    """Create package nodes for every dotted prefix and link parent -> child."""
    parts = module_path.split(".")
    parent_id: str | None = None
    for i in range(1, len(parts) + 1):
        node_id = ".".join(parts[:i])
        node = nodes.get(node_id)
        if node is None:
            node = TreeNode(
                symbol_id=node_id,
                name=parts[i - 1],
                kind=SymbolKind.MODULE,
            )
            nodes[node_id] = node
        if parent_id is not None:
            node.parent_id = parent_id
            parent = nodes[parent_id]
            if node_id not in parent.children:
                parent.children.append(node_id)
        parent_id = node_id


def _compute_subtree_hash(nodes: dict[str, TreeNode], node_id: str) -> str:
    """Hash a node from its own source hash + sorted child subtree hashes."""
    node = nodes[node_id]
    node.children.sort()
    parts = [node.name, node.file_hash or ""]
    for child_id in node.children:
        child_hash = _compute_subtree_hash(nodes, child_id)
        parts.append(f"{nodes[child_id].name}={child_hash}")
    node.subtree_hash = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()
    return node.subtree_hash


def load_or_rebuild(index_store: IndexStore, tree_store: TreeStore) -> RebuildResult:
    """Return a tree consistent with the current indexes, rebuilding minimally.

    Fast path: if the set of indexed files and their hashes match the cached
    tree, the cached tree is returned untouched (no node reconstruction). Else a
    fresh tree is built and subtrees whose ``subtree_hash`` is unchanged are
    reused from the cache; only the changed/removed subtrees are persisted.
    """
    indexes: list[FileIndex] = []
    for path in index_store.list_indexed_files():
        idx = index_store.load(path)
        if idx is not None:
            indexes.append(idx)
    cached = tree_store.load()

    if cached is not None:
        current_manifest = {i.file_path: i.file_hash for i in indexes}
        cached_manifest = {
            n.file_path: n.file_hash
            for n in cached.nodes.values()
            if n.file_path is not None
        }
        if current_manifest == cached_manifest:
            return RebuildResult(tree=cached, reused=set(cached.nodes))

    fresh = build_tree(indexes)

    if cached is None:
        tree_store.save(fresh)
        return RebuildResult(tree=fresh, rebuilt=set(fresh.nodes))

    rebuilt: set[str] = set()
    reused: set[str] = set()
    for node_id, node in fresh.nodes.items():
        cached_node = cached.nodes.get(node_id)
        if cached_node is not None and cached_node.subtree_hash == node.subtree_hash:
            fresh.nodes[node_id] = cached_node
            reused.add(node_id)
        else:
            rebuilt.add(node_id)
    removed = set(cached.nodes) - set(fresh.nodes)

    if rebuilt or removed:
        tree_store.save(fresh)

    return RebuildResult(tree=fresh, rebuilt=rebuilt, reused=reused, removed=removed)
