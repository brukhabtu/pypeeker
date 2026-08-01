"""Cross-file symbol-tree storage.

Persists the package/module skeleton to ``.pypeeker/tree.json`` — a single
artifact separate from the per-file ``index/*.json`` files managed by
:class:`pypeeker.storage.index_store.IndexStore`.
"""

from __future__ import annotations

from pathlib import Path

from pypeeker.models import TreeIndex, from_json, to_json

TREE_FILE = "tree.json"


class TreeStore:
    """The persisted symbol tree under ``.pypeeker/tree.json``."""

    def __init__(self, project_root: Path) -> None:
        # Deferred on purpose: index_store.py needs the TreeStore *type* at
        # definition time (its `-> TreeStore` return annotation is checked by
        # ruff, so no deferral there can satisfy it), while this constructor
        # only needs resolve_storage_root's *value* at call time — so the
        # deferral belongs on this side. A module-level import here would be
        # a real cycle (tree_store -> index_store -> ... -> tree_store via
        # its TreeStore import), and hoisting it back to module level (even
        # under TYPE_CHECKING) recreates that cycle; self-lint's
        # no-import-cycles rule ignores function-local imports by design, so
        # it will NOT catch that regression — don't move this back.
        from pypeeker.storage.index_store import resolve_storage_root

        self._tree_path = resolve_storage_root(project_root) / TREE_FILE

    def save(self, tree: TreeIndex) -> Path:
        """Persist the tree, creating ``.pypeeker/`` if needed."""
        self._tree_path.parent.mkdir(parents=True, exist_ok=True)
        self._tree_path.write_text(to_json(tree, indent=2))
        return self._tree_path

    def load(self) -> TreeIndex | None:
        """Load the persisted tree, or None if it has never been built."""
        if not self._tree_path.exists():
            return None
        return from_json(TreeIndex, self._tree_path.read_text())


class InMemoryTreeStore:
    """A :class:`TreeStore`-compatible tree cache that never touches disk.

    Structurally, not nominally, compatible with :class:`TreeStore` (same
    ``save``/``load`` shape) — it exists so a simulation store
    (:class:`~pypeeker.storage.overlay.OverlayIndexStore`) can answer
    ``default_tree_store()`` without constructing a ``.pypeeker/tree.json``
    path from the real ``project_root`` it wraps, which would otherwise
    persist a simulated tree into the user's real tree artifact. ``save``
    returns the path the tree *would* occupy, mirroring
    :meth:`~pypeeker.storage.overlay.OverlayIndexStore.save`, and writes
    nothing. A fresh instance's :meth:`load` returning ``None`` is
    deliberate: it makes ``treebuild._reconcile_tree`` build the tree from
    the overlay's own indexes rather than reconcile against the real
    project's cached tree.
    """

    def __init__(self) -> None:
        self._tree: TreeIndex | None = None

    def save(self, tree: TreeIndex) -> Path:
        """Cache ``tree`` in memory and return the path it would occupy on disk."""
        self._tree = tree
        return Path(TREE_FILE)

    def load(self) -> TreeIndex | None:
        """Return the cached tree, or None if nothing has been saved yet."""
        return self._tree
