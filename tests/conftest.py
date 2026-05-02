"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from pypeeker.adapters.python_adapter import PythonAdapter
from pypeeker.binder.binder import Binder
from pypeeker.models.index import FileIndex
from pypeeker.storage.store import IndexStore

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def adapter():
    return PythonAdapter()


@pytest.fixture
def project_dir(tmp_path):
    """A temporary project directory with .semantic-tool/index/ created."""
    (tmp_path / ".semantic-tool" / "index").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def store(project_dir):
    return IndexStore(project_dir)


@pytest.fixture
def bind_source(adapter):
    """Helper fixture that parses and binds a source string, returns FileIndex."""

    def _bind(source: str, file_path: str = "test.py") -> FileIndex:
        source_bytes = source.encode("utf-8")
        tree = adapter.parse(source_bytes)
        binder = Binder(adapter, file_path, source_bytes)
        return binder.bind(tree.root_node)

    return _bind


@pytest.fixture
def bind_fixture(adapter):
    """Bind a fixture file and return the FileIndex."""

    def _bind(fixture_name: str) -> FileIndex:
        fixture_path = FIXTURES_DIR / fixture_name
        source = fixture_path.read_bytes()
        tree = adapter.parse(source)
        binder = Binder(adapter, fixture_name, source)
        return binder.bind(tree.root_node)

    return _bind


@pytest.fixture
def analysis_context(indexed_project):
    """Build an AnalysisContext for a single function in an inline source.

    Returns a callable: ``analysis_context(src, "mod.py:f") -> AnalysisContext``.
    Used by purity / fact / call-graph tests to avoid repeating the same
    indexed_project + AnalysisContext.for_function dance.
    """
    from pypeeker.analysis import AnalysisContext, ContextError

    def _build(src: str, symbol_id: str, file_name: str = "mod.py"):
        _, store = indexed_project({file_name: src})
        ctx = AnalysisContext.for_function(store, symbol_id)
        assert not isinstance(ctx, ContextError), ctx
        return ctx

    return _build


@pytest.fixture
def indexed_project(tmp_path, adapter):
    """Create a project with source files and index them.

    Returns a callable that accepts a dict of {filename: source_code}
    and returns (project_dir, store).
    """

    def _setup(files: dict[str, str]) -> tuple[Path, IndexStore]:
        (tmp_path / ".semantic-tool" / "index").mkdir(parents=True, exist_ok=True)
        store = IndexStore(tmp_path)
        for name, content in files.items():
            p = tmp_path / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
            source_bytes = content.encode("utf-8")
            tree = adapter.parse(source_bytes)
            binder = Binder(adapter, name, source_bytes)
            file_index = binder.bind(tree.root_node)
            store.save(file_index)
        return tmp_path, store

    return _setup
