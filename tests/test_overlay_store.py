"""Tests for the overlay IndexStore (in-memory VFS for simulation).

Covers the file-bytes layering, the in-memory index layer and its isolation
from the base store and the disk, ``is_stale`` transitions across
``write_file`` -> ``rebind``, ``list_indexed_files`` semantics, and an
end-to-end smoke test of the query engine over an overlay store.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pypeeker.indexer import index_path
from pypeeker.query.engine import SemanticQueryEngine
from pypeeker.refactor.simulate import _rebind as rebind
from pypeeker.storage import IndexStore, OverlayIndexStore


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    """Map every file under ``root`` (recursively) to its exact bytes."""
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.fixture
def project(tmp_path):
    """A tiny on-disk project with one indexed module.

    Returns ``(root, base_store, overlay)`` where ``mod.py`` is indexed in the
    base store (on disk) and the overlay wraps that base store.
    """
    (tmp_path / "mod.py").write_text("def greet():\n    return 'hi'\n")
    base = IndexStore(tmp_path)
    result = index_path(tmp_path / "mod.py", store=base, root=tmp_path, src_roots=())
    assert result.indexed == ["mod.py"]
    return tmp_path, base, OverlayIndexStore(base)


# ---------------------------------------------------------------------------
# File-bytes layering
# ---------------------------------------------------------------------------


def test_read_file_passes_through_to_disk(project):
    root, _base, overlay = project
    assert overlay.read_file("mod.py") == (root / "mod.py").read_bytes()


def test_write_file_shadows_disk_without_touching_it(project):
    root, _base, overlay = project
    original = (root / "mod.py").read_bytes()
    overlay.write_file("mod.py", b"def greet():\n    return 'bye'\n")
    assert overlay.read_file("mod.py") == b"def greet():\n    return 'bye'\n"
    assert (root / "mod.py").read_bytes() == original  # disk untouched


def test_write_file_can_create_new_virtual_file(project):
    root, _base, overlay = project
    overlay.write_file("new.py", b"X = 1\n")
    assert overlay.read_file("new.py") == b"X = 1\n"
    assert overlay.file_exists("new.py")
    assert not (root / "new.py").exists()


def test_delete_file_masks_disk_file(project):
    root, _base, overlay = project
    overlay.delete_file("mod.py")
    assert not overlay.file_exists("mod.py")
    with pytest.raises(FileNotFoundError):
        overlay.read_file("mod.py")
    assert (root / "mod.py").exists()  # disk untouched


def test_write_after_delete_resurrects_path(project):
    _root, _base, overlay = project
    overlay.delete_file("mod.py")
    overlay.write_file("mod.py", b"Y = 2\n")
    assert overlay.read_file("mod.py") == b"Y = 2\n"


def test_read_file_missing_everywhere_raises(project):
    _root, _base, overlay = project
    with pytest.raises(FileNotFoundError):
        overlay.read_file("missing.py")


# ---------------------------------------------------------------------------
# Index layer: read-through + isolation
# ---------------------------------------------------------------------------


def test_load_passes_through_to_base(project):
    _root, base, overlay = project
    assert overlay.load("mod.py") is not None
    assert overlay.load("mod.py").file_hash == base.load("mod.py").file_hash


def test_save_and_remove_do_not_touch_disk_or_base(project):
    root, base, overlay = project
    base_index_before = base.load("mod.py")
    disk_before = _snapshot_tree(root / ".semantic-tool")

    overlay.write_file("mod.py", b"def greet():\n    return 'bye'\n")
    new_index = rebind(overlay, "mod.py", src_roots=())
    assert overlay.load("mod.py") is new_index
    overlay.write_file("extra.py", b"Z = 3\n")
    rebind(overlay, "extra.py", src_roots=())
    overlay.remove("mod.py")
    overlay.remove("extra.py")

    # Disk byte-for-byte untouched, base store still serves the original.
    assert _snapshot_tree(root / ".semantic-tool") == disk_before
    assert base.load("mod.py") is base_index_before
    assert base.load("extra.py") is None
    assert base.list_indexed_files() == ["mod.py"]


def test_save_returns_virtual_index_path_without_writing(project):
    root, _base, overlay = project
    overlay.write_file("pkg/new.py", b"A = 1\n")
    index = rebind(overlay, "pkg/new.py", src_roots=())
    path = overlay.save(index)
    assert path == root / ".semantic-tool" / "index" / "pkg" / "new.py.json"
    assert not path.exists()


def test_remove_shadows_base_entry(project):
    _root, base, overlay = project
    overlay.remove("mod.py")
    assert overlay.load("mod.py") is None
    assert base.load("mod.py") is not None  # base untouched


def test_save_after_remove_restores_entry(project):
    _root, _base, overlay = project
    original = overlay.load("mod.py")
    overlay.remove("mod.py")
    overlay.save(original)
    assert overlay.load("mod.py") is original


def test_project_root_and_hash_delegate_to_base(project):
    root, base, overlay = project
    assert overlay.project_root == base.project_root == root
    assert overlay.compute_file_hash(root / "mod.py") == base.compute_file_hash(
        root / "mod.py"
    )


# ---------------------------------------------------------------------------
# is_stale transitions
# ---------------------------------------------------------------------------


def test_is_stale_transitions_across_write_and_rebind(project):
    _root, base, overlay = project
    assert not overlay.is_stale("mod.py")  # fresh via base index + disk bytes

    overlay.write_file("mod.py", b"def greet():\n    return 'bye'\n")
    assert overlay.is_stale("mod.py")  # overlay content hash != indexed hash

    rebind(overlay, "mod.py", src_roots=())
    assert not overlay.is_stale("mod.py")  # in-memory index matches overlay

    assert not base.is_stale("mod.py")  # base view never changed


def test_is_stale_true_for_unindexed_and_deleted(project):
    _root, _base, overlay = project
    assert overlay.is_stale("never_indexed.py")
    overlay.delete_file("mod.py")
    assert overlay.is_stale("mod.py")
    overlay.remove("mod.py")
    assert overlay.is_stale("mod.py")


# ---------------------------------------------------------------------------
# list_indexed_files semantics
# ---------------------------------------------------------------------------


def test_list_indexed_files_adds_and_removes(project):
    _root, base, overlay = project
    assert overlay.list_indexed_files() == ["mod.py"]

    overlay.write_file("added.py", b"B = 1\n")
    rebind(overlay, "added.py", src_roots=())
    assert overlay.list_indexed_files() == ["added.py", "mod.py"]

    overlay.remove("mod.py")
    assert overlay.list_indexed_files() == ["added.py"]

    overlay.remove("added.py")
    assert overlay.list_indexed_files() == []

    assert base.list_indexed_files() == ["mod.py"]  # base untouched


# ---------------------------------------------------------------------------
# rebind correctness
# ---------------------------------------------------------------------------


def test_rebind_binds_overlay_content(project):
    _root, _base, overlay = project
    overlay.write_file("mod.py", b"def farewell():\n    return 'bye'\n")
    index = rebind(overlay, "mod.py", src_roots=())
    names = {s.name for s in index.symbols}
    assert "farewell" in names
    assert "greet" not in names
    assert index.file_path == "mod.py"


# ---------------------------------------------------------------------------
# Engine smoke test: read-through contract
# ---------------------------------------------------------------------------


def test_query_engine_sees_overlay_world_not_real_store(project):
    _root, base, overlay = project
    overlay.write_file(
        "mod.py", b"def greet():\n    return 'hi'\n\ndef shiny_new():\n    pass\n"
    )
    rebind(overlay, "mod.py", src_roots=())
    overlay.write_file("virt.py", b"def virtual_only():\n    pass\n")
    rebind(overlay, "virt.py", src_roots=())

    overlay_engine = SemanticQueryEngine(overlay)
    assert overlay_engine.find_symbol("shiny_new")
    assert overlay_engine.find_symbol("virtual_only")
    assert overlay_engine.find_symbol("greet")

    real_engine = SemanticQueryEngine(base)
    assert real_engine.find_symbol("greet")
    assert not real_engine.find_symbol("shiny_new")
    assert not real_engine.find_symbol("virtual_only")


def test_query_engine_references_resolve_through_overlay(project):
    _root, _base, overlay = project
    overlay.write_file(
        "mod.py",
        b"def greet():\n    return 'hi'\n\ndef caller():\n    return greet()\n",
    )
    rebind(overlay, "mod.py", src_roots=())
    engine = SemanticQueryEngine(overlay)
    refs = engine.references_to_definition("mod:greet")
    assert any(r.location.file_path == "mod.py" for r in refs)
