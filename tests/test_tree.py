"""Tests for the cross-file symbol tree."""

from __future__ import annotations

from pypeeker.binder.binder import bind
from pypeeker.models.symbols import SymbolKind
from pypeeker.models.tree import TreeIndex
from pypeeker.query.engine import SemanticQueryEngine
from pypeeker.storage import IndexStore, TreeStore
from pypeeker.tree import build_tree, load_or_rebuild


def _index(store, adapter, root, rel_path, source, *, module_path=None):
    """Bind + persist a source file the way the indexer would."""
    source_bytes = source.encode("utf-8")
    tree = adapter.parse(source_bytes)
    file_index = bind(adapter, rel_path, source_bytes, tree.root_node, module_path=module_path)
    store.save(file_index)
    real = root / rel_path
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_bytes(source_bytes)
    return file_index


# ── AC1: module is a first-class MODULE symbol ──────────────────────────────


def test_module_emitted_as_symbol(bind_source):
    index = bind_source("def foo(): pass\n", file_path="pkg/mod.py")
    modules = [s for s in index.symbols if s.kind == SymbolKind.MODULE]
    assert len(modules) == 1
    assert modules[0].symbol_id == "pkg.mod"
    assert modules[0].name == "mod"


def test_module_symbol_id_matches_module_scope(bind_source):
    index = bind_source("x = 1\n", file_path="a/b.py")
    module = next(s for s in index.symbols if s.kind == SymbolKind.MODULE)
    module_scope = next(s for s in index.scopes if s.kind.value == "module")
    assert module.symbol_id == module_scope.scope_id


def test_module_symbol_captures_docstring(bind_source):
    index = bind_source('"""Top docstring."""\nx = 1\n', file_path="m.py")
    module = next(s for s in index.symbols if s.kind == SymbolKind.MODULE)
    assert module.docstring == "Top docstring."


# ── AC2: package/module skeleton with parent/child links ────────────────────


def _build_from(adapter, files: dict[str, str]) -> TreeIndex:
    indexes = []
    for rel, src in files.items():
        b = src.encode("utf-8")
        t = adapter.parse(b)
        from pypeeker.binder.helpers import module_path_from

        indexes.append(
            bind(adapter, rel, b, t.root_node, module_path=module_path_from(rel, ("src",)))
        )
    return build_tree(indexes)


def test_packages_synthesized_for_every_prefix(adapter):
    tree = _build_from(
        adapter,
        {
            "src/pkg/sub/mod.py": "x = 1\n",
            "src/pkg/other.py": "y = 1\n",
        },
    )
    assert set(tree.nodes) == {"pkg", "pkg.sub", "pkg.sub.mod", "pkg.other"}
    assert tree.nodes["pkg"].kind == SymbolKind.PACKAGE
    assert tree.nodes["pkg.sub"].kind == SymbolKind.PACKAGE
    assert tree.nodes["pkg.sub.mod"].kind == SymbolKind.MODULE
    assert tree.root_ids == ["pkg"]


def test_parent_child_links(adapter):
    tree = _build_from(adapter, {"src/pkg/sub/mod.py": "x = 1\n"})
    assert tree.nodes["pkg"].parent_id is None
    assert tree.nodes["pkg.sub"].parent_id == "pkg"
    assert tree.nodes["pkg.sub.mod"].parent_id == "pkg.sub"
    assert tree.nodes["pkg"].children == ["pkg.sub"]
    assert tree.nodes["pkg.sub"].children == ["pkg.sub.mod"]


def test_init_is_package_and_module(adapter):
    """A package's __init__.py and its submodule collapse onto one node."""
    tree = _build_from(
        adapter,
        {
            "src/pkg/__init__.py": "ROOT = 1\n",
            "src/pkg/mod.py": "y = 1\n",
        },
    )
    pkg = tree.nodes["pkg"]
    assert pkg.kind == SymbolKind.PACKAGE
    assert pkg.file_path == "src/pkg/__init__.py"  # backed by __init__
    assert pkg.children == ["pkg.mod"]


# ── AC4: persistence round-trip ─────────────────────────────────────────────


def test_tree_store_round_trip(project_dir, adapter):
    tree = _build_from(adapter, {"src/pkg/mod.py": "x = 1\n"})
    store = TreeStore(project_dir)
    path = store.save(tree)
    assert path.exists()
    assert path.name == "tree.json"
    loaded = store.load()
    assert loaded is not None
    assert set(loaded.nodes) == set(tree.nodes)
    assert loaded.nodes["pkg.mod"].subtree_hash == tree.nodes["pkg.mod"].subtree_hash


def test_tree_store_load_missing(project_dir):
    assert TreeStore(project_dir).load() is None


# ── AC5: membership-aware subtree hashing ───────────────────────────────────


def test_edit_changes_only_ancestors(project_dir, adapter):
    files = {
        "src/pkg/a/one.py": "x = 1\n",
        "src/pkg/b/two.py": "y = 1\n",
    }
    before = _build_from(adapter, files)
    files["src/pkg/a/one.py"] = "x = 2  # edited\n"
    after = _build_from(adapter, files)

    # Changed module + its ancestors get new hashes.
    assert after.nodes["pkg.a.one"].subtree_hash != before.nodes["pkg.a.one"].subtree_hash
    assert after.nodes["pkg.a"].subtree_hash != before.nodes["pkg.a"].subtree_hash
    assert after.nodes["pkg"].subtree_hash != before.nodes["pkg"].subtree_hash
    # Sibling subtree is byte-identical.
    assert after.nodes["pkg.b"].subtree_hash == before.nodes["pkg.b"].subtree_hash
    assert after.nodes["pkg.b.two"].subtree_hash == before.nodes["pkg.b.two"].subtree_hash


def test_add_member_changes_only_ancestors(adapter):
    before = _build_from(adapter, {"src/pkg/a/one.py": "x = 1\n", "src/pkg/b/two.py": "y = 1\n"})
    after = _build_from(
        adapter,
        {
            "src/pkg/a/one.py": "x = 1\n",
            "src/pkg/a/three.py": "z = 1\n",
            "src/pkg/b/two.py": "y = 1\n",
        },
    )
    assert after.nodes["pkg.a"].subtree_hash != before.nodes["pkg.a"].subtree_hash
    assert after.nodes["pkg.b"].subtree_hash == before.nodes["pkg.b"].subtree_hash


# ── AC6/AC7: incremental rebuild on read ────────────────────────────────────


def test_first_read_builds_and_persists(project_dir, adapter):
    store = IndexStore(project_dir)
    tree_store = TreeStore(project_dir)
    _index(store, adapter, project_dir, "src/pkg/mod.py", "x = 1\n", module_path="pkg.mod")

    result = load_or_rebuild(store, tree_store)
    assert result.rebuilt == set(result.tree.nodes)
    assert tree_store.load() is not None


def test_no_change_read_is_fast_path(project_dir, adapter):
    store = IndexStore(project_dir)
    tree_store = TreeStore(project_dir)
    _index(store, adapter, project_dir, "src/pkg/mod.py", "x = 1\n", module_path="pkg.mod")
    load_or_rebuild(store, tree_store)

    again = load_or_rebuild(store, tree_store)
    assert again.rebuilt == set()
    assert again.removed == set()
    assert again.reused == set(again.tree.nodes)


def test_edit_rebuilds_only_affected_subtree(project_dir, adapter):
    store = IndexStore(project_dir)
    tree_store = TreeStore(project_dir)
    _index(store, adapter, project_dir, "src/pkg/a/one.py", "x = 1\n", module_path="pkg.a.one")
    _index(store, adapter, project_dir, "src/pkg/b/two.py", "y = 1\n", module_path="pkg.b.two")
    load_or_rebuild(store, tree_store)

    _index(store, adapter, project_dir, "src/pkg/a/one.py", "x = 2\n", module_path="pkg.a.one")
    result = load_or_rebuild(store, tree_store)

    assert result.rebuilt == {"pkg", "pkg.a", "pkg.a.one"}
    assert "pkg.b" in result.reused
    assert "pkg.b.two" in result.reused


def test_added_file_detected(project_dir, adapter):
    store = IndexStore(project_dir)
    tree_store = TreeStore(project_dir)
    _index(store, adapter, project_dir, "src/pkg/a.py", "x = 1\n", module_path="pkg.a")
    load_or_rebuild(store, tree_store)

    _index(store, adapter, project_dir, "src/pkg/b.py", "y = 1\n", module_path="pkg.b")
    result = load_or_rebuild(store, tree_store)
    assert "pkg.b" in result.rebuilt
    assert "pkg.b" in result.tree.nodes


def test_removed_file_detected(project_dir, adapter):
    store = IndexStore(project_dir)
    tree_store = TreeStore(project_dir)
    _index(store, adapter, project_dir, "src/pkg/a.py", "x = 1\n", module_path="pkg.a")
    _index(store, adapter, project_dir, "src/pkg/b.py", "y = 1\n", module_path="pkg.b")
    load_or_rebuild(store, tree_store)

    store.remove("src/pkg/b.py")
    result = load_or_rebuild(store, tree_store)
    assert "pkg.b" in result.removed
    assert "pkg.b" not in result.tree.nodes


# ── AC3/AC8: query surface composes tree with per-file structure ────────────


def test_get_tree_and_members(project_dir, adapter):
    store = IndexStore(project_dir)
    _index(
        store,
        adapter,
        project_dir,
        "src/pkg/mod.py",
        "class Foo:\n    def bar(self):\n        x = 1\n",
        module_path="pkg.mod",
    )
    engine = SemanticQueryEngine(store)

    tree = engine.get_tree()
    assert tree.root_ids == ["pkg"]

    # package -> module
    pkg_members = engine.members("pkg")
    assert [m["symbol_id"] for m in pkg_members] == ["pkg.mod"]

    # module -> top-level symbols
    mod_members = engine.members("pkg.mod")
    names = {m["name"] for m in mod_members}
    assert "Foo" in names

    # class -> methods (below the module boundary, via parent_scope_id)
    class_members = engine.members("pkg.mod:Foo")
    assert [m["name"] for m in class_members] == ["bar"]


def test_document_symbols(project_dir, adapter):
    store = IndexStore(project_dir)
    _index(
        store,
        adapter,
        project_dir,
        "src/pkg/mod.py",
        "TOP = 1\ndef helper(): pass\n",
        module_path="pkg.mod",
    )
    engine = SemanticQueryEngine(store)
    names = {s["name"] for s in engine.document_symbols("pkg.mod")}
    assert "TOP" in names
    assert "helper" in names
    assert "mod" not in names  # the module symbol itself is excluded
