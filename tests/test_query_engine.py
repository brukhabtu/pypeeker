"""Tests for the query engine."""

from pypeeker.binder.binder import Binder
from pypeeker.adapters.python_adapter import PythonAdapter
from pypeeker.query.engine import SemanticQueryEngine


def _index_source(store, source: str, file_path: str = "test.py"):
    """Helper to index a source string."""
    adapter = PythonAdapter()
    source_bytes = source.encode("utf-8")
    tree = adapter.parse(source_bytes)
    binder = Binder(adapter, file_path, source_bytes)
    index = binder.bind(tree.root_node)
    store.save(index)

    # Also write the source file so staleness checks work
    real_path = store.project_root / file_path
    real_path.parent.mkdir(parents=True, exist_ok=True)
    real_path.write_bytes(source_bytes)

    return index


def test_find_symbol_by_name(store):
    _index_source(store, "def greet(): pass\n")
    engine = SemanticQueryEngine(store)
    results = engine.find_symbol("greet")
    assert len(results) == 1
    assert results[0].name == "greet"


def test_find_symbol_by_id(store):
    _index_source(store, "def greet(): pass\n")
    engine = SemanticQueryEngine(store)
    results = engine.find_symbol("test.py:greet")
    assert len(results) == 1


def test_find_symbol_partial_match(store):
    source = "class Auth:\n    def validate(self): pass\n"
    _index_source(store, source)
    engine = SemanticQueryEngine(store)
    results = engine.find_symbol("Auth.validate")
    assert len(results) == 1
    assert results[0].symbol_id == "test.py:Auth.validate"


def test_find_symbol_across_files(store):
    _index_source(store, "def foo(): pass\n", "a.py")
    _index_source(store, "def foo(): pass\n", "b.py")
    engine = SemanticQueryEngine(store)
    results = engine.find_symbol("foo")
    assert len(results) == 2


def test_find_references(store):
    source = "def greet(): pass\ngreet()\n"
    _index_source(store, source)
    engine = SemanticQueryEngine(store)
    refs = engine.find_references("test.py:greet")
    assert len(refs) >= 1
    assert any(r.kind.value == "call" for r in refs)


def test_get_scope_at_function(store):
    source = "x = 1\ndef foo():\n    y = 2\n    return y\n"
    _index_source(store, source)
    engine = SemanticQueryEngine(store)
    result = engine.get_scope_at("test.py", 2)
    assert "error" not in result
    assert result["scope"]["name"] == "foo"


def test_get_scope_at_module(store):
    source = "x = 1\ny = 2\n"
    _index_source(store, source)
    engine = SemanticQueryEngine(store)
    result = engine.get_scope_at("test.py", 0)
    assert result["scope"]["kind"] == "module"


def test_scope_visible_symbols(store):
    source = "x = 1\ndef foo():\n    y = 2\n    return x + y\n"
    _index_source(store, source)
    engine = SemanticQueryEngine(store)
    result = engine.get_scope_at("test.py", 2)
    visible_names = {s["name"] for s in result["visible_symbols"]}
    assert "y" in visible_names
    assert "x" in visible_names
    assert "foo" in visible_names


def test_scope_chain(store):
    source = "class C:\n    def m(self):\n        x = 1\n"
    _index_source(store, source)
    engine = SemanticQueryEngine(store)
    result = engine.get_scope_at("test.py", 2)
    chain_names = [s["name"] for s in result["scope_chain"]]
    assert chain_names[0] == "m"  # innermost
    assert "C" in chain_names
    assert "test.py" in chain_names  # module


def test_get_scope_not_indexed(store):
    engine = SemanticQueryEngine(store)
    result = engine.get_scope_at("nonexistent.py", 0)
    assert "error" in result
