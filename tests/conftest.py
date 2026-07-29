"""Shared test fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pypeeker.adapters.python_adapter import PythonAdapter
from pypeeker.binder.binder import bind
from pypeeker.models.index import FileIndex
from pypeeker.storage import IndexStore, TransactionStore

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _restore_cwd():
    """Restore the process working directory after every test.

    Many CLI tests call raw ``os.chdir(tmp_path)`` without restoring it, which
    leaks the working directory into whatever test runs next (see
    ``review/03-test-coupling.md`` §4a). This autouse guard records ``getcwd()``
    before the test and restores it on teardown, neutralizing that leakage class
    regardless of whether a test restores cwd itself.
    """
    original = os.getcwd()
    try:
        yield
    finally:
        os.chdir(original)


def sym(
    module: str,
    *scopes: str,
    local: str | None = None,
    shadow: int | None = None,
) -> str:
    """Build a symbol-id string from its parts, per the grammar in
    ``pypeeker.models.symbol_id`` (``module.path:Scope.Chain:local$N``).

    * ``module`` — the dotted module path (before the first ``:``).
    * ``scopes`` — dot-separated scope-creating names (classes, functions)
      attached after the first ``:``; omit for a bare module id.
    * ``local`` — a non-scope-creating name (variable, parameter) attached with
      a second ``:``.
    * ``shadow`` — an optional shadow ordinal, rendered as the ``$N`` suffix
      (``2`` -> ``x$2``); the first binding carries no suffix, so pass ``None``.

    Additive test helper — existing tests keep their hand-typed literals; new
    tests may build ids here so a grammar change touches one place.

    Examples::

        sym("mod", "f")                          # "mod:f"
        sym("lib", "Svc", "run")                 # "lib:Svc.run"
        sym("mod", "f", local="x")               # "mod:f:x"
        sym("mod", "f", local="x", shadow=2)     # "mod:f:x$2"
    """
    symbol_id = module
    if scopes:
        symbol_id += ":" + ".".join(scopes)
    if local is not None:
        symbol_id += ":" + local
    if shadow is not None:
        symbol_id += f"${shadow}"
    return symbol_id


@pytest.fixture
def sym_builder():
    """Expose :func:`sym` as a fixture for tests that prefer injection."""
    return sym


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
def transaction_store(project_dir):
    return TransactionStore(project_dir)


@pytest.fixture
def bind_source(adapter):
    """Helper fixture that parses and binds a source string, returns FileIndex."""

    def _bind(source: str, file_path: str = "test.py") -> FileIndex:
        source_bytes = source.encode("utf-8")
        tree = adapter.parse(source_bytes)
        return bind(adapter, file_path, source_bytes, tree.root_node)

    return _bind


@pytest.fixture
def bind_fixture(adapter):
    """Bind a fixture file and return the FileIndex."""

    def _bind(fixture_name: str) -> FileIndex:
        fixture_path = FIXTURES_DIR / fixture_name
        source = fixture_path.read_bytes()
        tree = adapter.parse(source)
        return bind(adapter, fixture_name, source, tree.root_node)

    return _bind


@pytest.fixture
def analysis_context(indexed_project):
    """Build an AnalysisContext for a single function in an inline source.

    Returns a callable: ``analysis_context(src, "mod:f") -> AnalysisContext``.
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
            file_index = bind(adapter, name, source_bytes, tree.root_node)
            store.save(file_index)
        return tmp_path, store

    return _setup
