"""Tests for the storage layer."""

import pytest

from pypeeker.models.index import FileIndex
from pypeeker.models.location import Location, Position, Span
from pypeeker.models.symbols import Symbol, SymbolKind, Visibility
from pypeeker.models.capabilities import Confidence

pytestmark = pytest.mark.integration


def _make_index(file_path="test.py", file_hash="abc123"):
    return FileIndex(
        file_path=file_path,
        file_hash=file_hash,
        language="python",
        symbols=[
            Symbol(
                symbol_id=f"{file_path}:foo",
                name="foo",
                kind=SymbolKind.FUNCTION,
                location=Location(
                    file_path=file_path,
                    span=Span(
                        start=Position(line=0, column=0),
                        end=Position(line=1, column=0),
                    ),
                ),
                visibility=Visibility.PUBLIC,
                visibility_confidence=Confidence.HEURISTIC,
                parent_scope_id=file_path,
            )
        ],
        scopes=[],
        references=[],
    )


def test_save_and_load(store):
    idx = _make_index()
    store.save(idx)
    loaded = store.load("test.py")
    assert loaded is not None
    assert loaded.file_path == "test.py"
    assert loaded.file_hash == "abc123"
    assert len(loaded.symbols) == 1
    assert loaded.symbols[0].name == "foo"


def test_load_nonexistent(store):
    loaded = store.load("nonexistent.py")
    assert loaded is None


def test_list_indexed_files(store):
    store.save(_make_index("a.py"))
    store.save(_make_index("b.py"))
    store.save(_make_index("src/c.py"))
    files = store.list_indexed_files()
    assert files == ["a.py", "b.py", "src/c.py"]


def test_remove(store):
    store.save(_make_index())
    assert store.load("test.py") is not None
    store.remove("test.py")
    assert store.load("test.py") is None


def test_staleness_no_index(store, project_dir):
    # No index exists — should be stale
    source_file = project_dir / "test.py"
    source_file.write_text("x = 1\n")
    assert store.is_stale("test.py")


def test_staleness_after_save(store, project_dir):
    source_file = project_dir / "test.py"
    content = "x = 1\n"
    source_file.write_text(content)

    import hashlib

    file_hash = hashlib.sha256(content.encode()).hexdigest()
    idx = _make_index(file_hash=file_hash)
    store.save(idx)
    assert not store.is_stale("test.py")


def test_staleness_after_modification(store, project_dir):
    source_file = project_dir / "test.py"
    content = "x = 1\n"
    source_file.write_text(content)

    import hashlib

    file_hash = hashlib.sha256(content.encode()).hexdigest()
    idx = _make_index(file_hash=file_hash)
    store.save(idx)

    # Modify the source
    source_file.write_text("x = 2\n")
    assert store.is_stale("test.py")


def test_directory_mirrors_source_structure(store):
    store.save(_make_index("src/auth/service.py"))
    index_path = store._source_to_index_path("src/auth/service.py")
    assert index_path.exists()
    assert "src/auth/service.py.json" in str(index_path)
