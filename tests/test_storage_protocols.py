"""The concrete stores satisfy the structural contracts consumers annotate against."""

from __future__ import annotations

from pypeeker.storage import (
    IndexStore,
    IndexStoreLike,
    InMemoryTreeStore,
    OverlayIndexStore,
    TreeStore,
    TreeStoreLike,
)


def test_index_stores_satisfy_index_store_like(project_dir):
    base = IndexStore(project_dir)
    assert isinstance(base, IndexStoreLike)
    assert isinstance(OverlayIndexStore(base), IndexStoreLike)


def test_tree_stores_satisfy_tree_store_like(project_dir):
    assert isinstance(TreeStore(project_dir), TreeStoreLike)
    assert isinstance(InMemoryTreeStore(), TreeStoreLike)
    assert isinstance(IndexStore(project_dir).default_tree_store(), TreeStoreLike)
    assert isinstance(
        OverlayIndexStore(IndexStore(project_dir)).default_tree_store(), TreeStoreLike
    )


