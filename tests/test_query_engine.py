"""Tests for the query engine."""

from pypeeker.binder.binder import bind
from pypeeker.adapters.python_adapter import PythonAdapter
from pypeeker.query import SemanticQueryEngine, symbol_matches


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


def test_engine_snapshot_survives_same_file_resave(store):
    """A re-save of an already-loaded file does not move the engine's snapshot.

    Corpus-wide queries answer from the index objects loaded on first use,
    so re-indexing ``mod.py`` through the same store is invisible to this
    engine's ``find_symbol`` — while ``get_scope_at``, a single-file read
    through the store's own cache, sees the new index. A fresh engine sees
    the re-save everywhere.
    """
    _index_source(store, "def old_name(): pass\n", "mod.py")
    engine = SemanticQueryEngine(store)
    assert len(engine.find_symbol("old_name")) == 1
    assert engine.find_symbol("new_name") == []

    _index_source(store, "def new_name(): pass\n", "mod.py")
    assert len(engine.find_symbol("old_name")) == 1
    assert engine.find_symbol("new_name") == []

    result = engine.get_scope_at("mod.py", 0)
    assert "error" not in result
    assert result["scope"]["name"] == "new_name"

    fresh = SemanticQueryEngine(store)
    assert fresh.find_symbol("old_name") == []
    assert len(fresh.find_symbol("new_name")) == 1


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
    assert (other_root / ".pypeeker" / "tree.json").exists()
    assert not (store.project_root / ".pypeeker" / "tree.json").exists()


def test_get_tree_default_tree_store_from_store_root(store):
    """Backward compat: omitting tree_store derives one from store.project_root
    once in __init__ (never ad hoc inside get_tree)."""
    _index_source(store, "def foo(): pass\n", "mod.py")
    engine = SemanticQueryEngine(store)
    tree = engine.get_tree()
    assert tree.nodes
    assert (store.project_root / ".pypeeker" / "tree.json").exists()


def test_in_memory_tree_store_round_trips_without_touching_disk(store, tmp_path):
    """An injected InMemoryTreeStore serves get_tree() from memory alone —
    no tree.json is ever created anywhere under tmp_path (TASK-133)."""
    from pypeeker.storage import InMemoryTreeStore

    tree_store = InMemoryTreeStore()
    _index_source(store, "def foo(): pass\n", "mod.py")
    assert tree_store.load() is None

    tree = SemanticQueryEngine(store, tree_store).get_tree()

    assert tree_store.load() is tree
    assert tree_store.save(tree).name == "tree.json"
    assert list(tmp_path.rglob("tree.json")) == []


def test_index_store_default_tree_store_persists_under_the_project_root(store):
    """IndexStore.default_tree_store() is the same disk-backed location the
    engine used to derive inline from store.project_root (TASK-133)."""
    _index_source(store, "def foo(): pass\n", "mod.py")
    tree_store = store.default_tree_store()
    tree = SemanticQueryEngine(store, tree_store).get_tree()
    tree_store.save(tree)
    assert (store.project_root / ".pypeeker" / "tree.json").exists()


def test_overlay_default_tree_store_is_one_cached_instance(store):
    """OverlayIndexStore.default_tree_store() hands back a non-persisting
    InMemoryTreeStore, and the same cached instance on repeat calls, so
    successive engines within one simulation reuse the built tree
    (TASK-133)."""
    from pypeeker.storage import InMemoryTreeStore, OverlayIndexStore

    overlay = OverlayIndexStore(store)
    first = overlay.default_tree_store()
    second = overlay.default_tree_store()
    assert isinstance(first, InMemoryTreeStore)
    assert first is second


# ---------------------------------------------------------------------------
# Snapshot contract: an engine is consistent as of first load
# ---------------------------------------------------------------------------


def test_engine_is_a_snapshot_across_corpus_wide_queries(store):
    """Every corpus-wide query answers from the index list loaded on first use.

    A store write made after the engine has loaded its indexes is invisible
    to that engine's ``find_symbol`` / ``references_to_binding`` /
    ``find_importers`` — the same frozen view the resolver-backed queries
    have always had — and visible to a freshly constructed engine.
    """
    _index_source(store, "def alpha(): pass\n", "a.py")
    engine = SemanticQueryEngine(store)
    assert [s.symbol_id for s in engine.find_symbol("alpha")] == ["a:alpha"]
    assert engine.references_to_binding("a:alpha") == []

    _index_source(store, "from a import alpha\nalpha()\n", "b.py")

    # The original engine keeps the world as of its first load.
    assert [s.symbol_id for s in engine.find_symbol("alpha")] == ["a:alpha"]
    assert engine.references_to_binding("b:alpha") == []
    assert engine.find_importers("a:alpha") == []
    assert [i.file_path for i in engine.all_indexes()] == ["a.py"]

    # A new engine over the same store sees the write.
    fresh = SemanticQueryEngine(store)
    assert sorted(s.symbol_id for s in fresh.find_symbol("alpha")) == ["a:alpha", "b:alpha"]
    assert len(fresh.references_to_binding("b:alpha")) == 1
    assert [s.symbol_id for s in fresh.find_importers("a:alpha")] == ["b:alpha"]


def test_single_file_reads_stay_live(store):
    """``get_scope_at`` reads one file through the store, so it sees later writes."""
    _index_source(store, "def alpha(): pass\n", "a.py")
    engine = SemanticQueryEngine(store)
    engine.find_symbol("alpha")  # populate the corpus snapshot
    assert "error" in engine.get_scope_at("b.py", 1)
    _index_source(store, "def beta(): pass\n", "b.py")
    assert engine.get_scope_at("b.py", 1)["scope"]["scope_id"] == "b"


# ---------------------------------------------------------------------------
# symbol_matches: the predicate behind find_symbol
# ---------------------------------------------------------------------------


def test_symbol_matches_four_ways(bind_source):
    index = bind_source("class Auth:\n    def validate(self): pass\n", "pkg/auth.py")
    (method,) = [s for s in index.symbols if s.name == "validate"]
    assert method.symbol_id == "pkg.auth:Auth.validate"
    assert symbol_matches(method, "validate")  # bare name
    assert symbol_matches(method, "pkg.auth:Auth.validate")  # exact id
    assert symbol_matches(method, "Auth.validate")  # ":"-anchored tail
    assert symbol_matches(method, "auth:Auth.validate")  # "."-anchored tail
    assert not symbol_matches(method, "Auth")
    assert not symbol_matches(method, "alidate")
    assert not symbol_matches(method, "pkg.auth:Auth")


def test_find_symbol_agrees_with_symbol_matches(store):
    _index_source(store, "class Auth:\n    def validate(self): pass\n", "pkg/auth.py")
    engine = SemanticQueryEngine(store)
    for name in ("validate", "Auth.validate", "auth:Auth.validate", "Auth", "nope"):
        expected = [
            s for index in engine.all_indexes() for s in index.symbols if symbol_matches(s, name)
        ]
        assert engine.find_symbol(name) == expected
