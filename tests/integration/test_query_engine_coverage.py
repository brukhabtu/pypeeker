"""Additional query engine integration tests for coverage gaps."""

import pytest

from pypeeker.binder.binder import Binder
from pypeeker.adapters.python_adapter import PythonAdapter
from pypeeker.query.engine import SemanticQueryEngine

pytestmark = pytest.mark.integration


def _index_source(store, source: str, file_path: str = "test.py"):
    """Helper to index a source string."""
    adapter = PythonAdapter()
    source_bytes = source.encode("utf-8")
    tree = adapter.parse(source_bytes)
    binder = Binder(adapter, file_path, source_bytes)
    index = binder.bind(tree.root_node)
    store.save(index)

    real_path = store.project_root / file_path
    real_path.parent.mkdir(parents=True, exist_ok=True)
    real_path.write_bytes(source_bytes)

    return index


class TestFindImportSymbols:
    def test_no_colon_in_symbol_id(self, store):
        """find_import_symbols with no colon should return empty list."""
        _index_source(store, "def foo(): pass\n")
        engine = SemanticQueryEngine(store)
        result = engine.find_import_symbols("foo")
        assert result == []

    def test_find_import_symbols_basic(self, store):
        """Find IMPORT symbols that import a given definition."""
        _index_source(store, "def helper():\n    pass\n", "lib.py")
        _index_source(store, "from lib import helper\n", "main.py")
        engine = SemanticQueryEngine(store)
        results = engine.find_import_symbols("lib.py:helper")
        assert len(results) >= 1
        assert any(s.name == "helper" for s in results)


class TestFindReexportLocations:
    def test_reexport_in_init_file(self, store):
        """Find re-export locations in __init__.py files."""
        _index_source(store, "class User:\n    pass\n", "models/user.py")
        _index_source(store, "from .user import User\n", "models/__init__.py")
        engine = SemanticQueryEngine(store)
        locations = engine.find_reexport_locations("models/user.py:User")
        assert len(locations) >= 1
        assert any(loc.file_path == "models/__init__.py" for loc in locations)

    def test_reexport_non_init_excluded(self, store):
        """Non-__init__.py imports should not appear in reexport locations."""
        _index_source(store, "def helper(): pass\n", "lib.py")
        _index_source(store, "from lib import helper\n", "main.py")
        engine = SemanticQueryEngine(store)
        locations = engine.find_reexport_locations("lib.py:helper")
        # main.py is not __init__.py, so should not appear
        assert all(loc.file_path.endswith("__init__.py") for loc in locations)

    def test_reexport_no_matches(self, store):
        """No imports means no reexport locations."""
        _index_source(store, "x = 1\n")
        engine = SemanticQueryEngine(store)
        locations = engine.find_reexport_locations("test.py:x")
        assert locations == []


class TestGetScopeEdgeCases:
    def test_scope_at_line_outside_all_scopes(self, store):
        """Line outside all named scopes should still find module scope."""
        _index_source(store, "x = 1\n")
        engine = SemanticQueryEngine(store)
        result = engine.get_scope_at("test.py", 0)
        assert "error" not in result
        assert result["scope"]["kind"] == "module"

    def test_scope_at_line_way_beyond_file(self, store):
        """Line number beyond file end should return no scope error."""
        _index_source(store, "x = 1\n")
        engine = SemanticQueryEngine(store)
        result = engine.get_scope_at("test.py", 9999)
        assert "error" in result

    def test_scope_chain_with_missing_parent(self, store):
        """Scope chain should handle missing parent gracefully."""
        _index_source(store, "def foo():\n    x = 1\n")
        engine = SemanticQueryEngine(store)
        result = engine.get_scope_at("test.py", 1)
        # Should build partial chain without crashing
        assert "scope_chain" in result
        assert len(result["scope_chain"]) >= 1
