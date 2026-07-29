"""Storage-directory resolution: the .pypeeker rename + .semantic-tool fallback.

The on-disk dir was renamed from ``.semantic-tool/`` to ``.pypeeker/``.
``resolve_storage_root`` prefers the new name but falls back to a pre-existing
legacy dir so projects indexed before the rename keep working without a manual
move. These tests pin that behavior (and the project-marker discovery) so the
fallback is not silently dropped by a later refactor.
"""

from __future__ import annotations

from pypeeker.indexer import find_project_root
from pypeeker.models import FileIndex
from pypeeker.storage.index_store import (
    LEGACY_STORAGE_DIR,
    STORAGE_DIR,
    IndexStore,
    resolve_storage_root,
)


def test_constants_are_the_expected_names():
    assert STORAGE_DIR == ".pypeeker"
    assert LEGACY_STORAGE_DIR == ".semantic-tool"


def test_fresh_project_resolves_to_new_dir(tmp_path):
    """Neither dir present: the new .pypeeker/ is the default."""
    assert resolve_storage_root(tmp_path) == tmp_path / STORAGE_DIR


def test_legacy_only_resolves_to_legacy_dir(tmp_path):
    """A pre-rename project keeps using its existing .semantic-tool/ dir."""
    (tmp_path / LEGACY_STORAGE_DIR).mkdir()
    assert resolve_storage_root(tmp_path) == tmp_path / LEGACY_STORAGE_DIR


def test_new_dir_wins_when_both_present(tmp_path):
    """Once .pypeeker/ exists it is authoritative, even beside a legacy dir."""
    (tmp_path / LEGACY_STORAGE_DIR).mkdir()
    (tmp_path / STORAGE_DIR).mkdir()
    assert resolve_storage_root(tmp_path) == tmp_path / STORAGE_DIR


def test_index_store_writes_new_dir_for_fresh_project(tmp_path):
    store = IndexStore(tmp_path)
    path = store.save(FileIndex(file_path="a.py", file_hash="h", language="python"))
    assert (tmp_path / STORAGE_DIR / "index" / "a.py.json") == path
    assert path.exists()
    assert not (tmp_path / LEGACY_STORAGE_DIR).exists()


def test_index_store_reads_and_writes_legacy_dir_when_present(tmp_path):
    """Legacy project: both reads and writes stay in .semantic-tool/ (no split)."""
    (tmp_path / LEGACY_STORAGE_DIR / "index").mkdir(parents=True)
    store = IndexStore(tmp_path)
    path = store.save(FileIndex(file_path="a.py", file_hash="h", language="python"))
    assert (tmp_path / LEGACY_STORAGE_DIR / "index" / "a.py.json") == path
    assert not (tmp_path / STORAGE_DIR).exists()
    assert store.load("a.py") is not None


def test_find_project_root_recognizes_both_markers(tmp_path):
    new = tmp_path / "new"
    (new / STORAGE_DIR).mkdir(parents=True)
    assert find_project_root(new) == new

    legacy = tmp_path / "legacy"
    (legacy / LEGACY_STORAGE_DIR).mkdir(parents=True)
    assert find_project_root(legacy) == legacy
