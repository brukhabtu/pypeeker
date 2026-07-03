"""Cross-file symbol-tree storage.

Persists the package/module skeleton to ``.semantic-tool/tree.json`` — a single
artifact separate from the per-file ``index/*.json`` files managed by
:class:`pypeeker.storage.index_store.IndexStore`.
"""

from __future__ import annotations

from pathlib import Path

from pypeeker.models import TreeIndex, from_json, to_json
from pypeeker.storage.index_store import SEMANTIC_TOOL_DIR

TREE_FILE = "tree.json"


class TreeStore:
    """The persisted symbol tree under ``.semantic-tool/tree.json``."""

    def __init__(self, project_root: Path) -> None:
        self._tree_path = project_root / SEMANTIC_TOOL_DIR / TREE_FILE

    def save(self, tree: TreeIndex) -> Path:
        """Persist the tree, creating ``.semantic-tool/`` if needed."""
        self._tree_path.parent.mkdir(parents=True, exist_ok=True)
        self._tree_path.write_text(to_json(tree, indent=2))
        return self._tree_path

    def load(self) -> TreeIndex | None:
        """Load the persisted tree, or None if it has never been built."""
        if not self._tree_path.exists():
            return None
        return from_json(TreeIndex, self._tree_path.read_text())
