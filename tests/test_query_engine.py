"""Tests for the query engine."""

from pypeeker.binder.binder import bind
from pypeeker.adapters.python_adapter import PythonAdapter
from pypeeker.query.engine import SemanticQueryEngine


def _index_source(store, source: str, file_path: str = "test.py"):
    """Helper to index a source string."""
    adapter = PythonAdapter()
    source_bytes = source.encode("utf-8")
    tree = adapter.parse(source_bytes)
    index = bind(adapter, file_path, source_bytes, tree.root_node)
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
    results = engine.find_symbol("test:greet")
    assert len(results) == 1


def test_find_symbol_partial_match(store):
    source = "class Auth:\n    def validate(self): pass\n"
    _index_source(store, source)
    engine = SemanticQueryEngine(store)
    results = engine.find_symbol("Auth.validate")
    assert len(results) == 1
    assert results[0].symbol_id == "test:Auth.validate"


def test_find_symbol_across_files(store):
    _index_source(store, "def foo(): pass\n", "a.py")
    _index_source(store, "def foo(): pass\n", "b.py")
    engine = SemanticQueryEngine(store)
    results = engine.find_symbol("foo")
    assert len(results) == 2


def test_references_to_binding(store):
    source = "def greet(): pass\ngreet()\n"
    _index_source(store, source)
    engine = SemanticQueryEngine(store)
    refs = engine.references_to_binding("test:greet")
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
    assert "test" in chain_names  # module (dotted module path)


def test_get_scope_not_indexed(store):
    engine = SemanticQueryEngine(store)
    result = engine.get_scope_at("nonexistent.py", 0)
    assert "error" in result


def test_engine_reads_reflect_store_save_through_same_store(store):
    """Per-file reads go through IndexStore's cache, so a save() made through
    the same store is visible to an already-constructed engine (the engine
    keeps no per-file index cache of its own)."""
    _index_source(store, "def old_name(): pass\n", "mod.py")
    engine = SemanticQueryEngine(store)
    assert len(engine.find_symbol("old_name")) == 1
    assert engine.find_symbol("new_name") == []

    # Re-index the same file through the same store; save() updates the
    # store's cache, and the engine reads through it.
    _index_source(store, "def new_name(): pass\n", "mod.py")
    assert engine.find_symbol("old_name") == []
    assert len(engine.find_symbol("new_name")) == 1

    # get_scope_at also reads through the store.
    result = engine.get_scope_at("mod.py", 0)
    assert "error" not in result
    assert result["scope"]["name"] == "new_name"


def test_get_tree_uses_injected_tree_store(store, tmp_path):
    """An injected TreeStore is the one the engine persists the tree through.

    Composition-root contract (TASK-63): the engine never builds storage ad
    hoc inside query methods — get_tree reads/writes through the TreeStore
    handed to __init__. We inject a TreeStore rooted elsewhere and assert the
    tree artifact lands there, not under the index store's project root.
    """
    from pypeeker.storage import TreeStore

    _index_source(store, "def foo(): pass\n", "mod.py")
    other_root = tmp_path / "elsewhere"
    other_root.mkdir()
    injected = TreeStore(other_root)

    engine = SemanticQueryEngine(store, injected)
    tree = engine.get_tree()

    assert tree.nodes  # the tree was actually built
    assert (other_root / ".semantic-tool" / "tree.json").exists()
    assert not (store.project_root / ".semantic-tool" / "tree.json").exists()


def test_get_tree_default_tree_store_from_store_root(store):
    """Backward compat: omitting tree_store derives one from store.project_root
    once in __init__ (never ad hoc inside get_tree)."""
    _index_source(store, "def foo(): pass\n", "mod.py")
    engine = SemanticQueryEngine(store)
    tree = engine.get_tree()
    assert tree.nodes
    assert (store.project_root / ".semantic-tool" / "tree.json").exists()
