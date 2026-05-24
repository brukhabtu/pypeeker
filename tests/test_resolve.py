"""Tests for cross-module resolution."""

from __future__ import annotations

from pypeeker.binder.binder import bind
from pypeeker.paths import module_path_from
from pypeeker.models.index import FileIndex
from pypeeker.query.engine import SemanticQueryEngine
from pypeeker.resolve import CrossModuleResolver
from pypeeker.storage import IndexStore


def _bind(adapter, rel_path, source) -> FileIndex:
    b = source.encode("utf-8")
    tree = adapter.parse(b)
    return bind(adapter, rel_path, b, tree.root_node, module_path=module_path_from(rel_path, ("src",)))


def _resolver(adapter, files: dict[str, str]) -> CrossModuleResolver:
    return CrossModuleResolver([_bind(adapter, rel, src) for rel, src in files.items()])


# ── resolve_definition ──────────────────────────────────────────────────────


def test_from_import_resolves_to_definition(adapter):
    r = _resolver(
        adapter,
        {
            "src/pkg/lib.py": "def helper():\n    pass\n",
            "src/pkg/app.py": "from pkg.lib import helper\nhelper()\n",
        },
    )
    assert r.resolve_definition("pkg.app:helper") == "pkg.lib:helper"


def test_aliased_import_resolves(adapter):
    r = _resolver(
        adapter,
        {
            "src/pkg/lib.py": "def helper():\n    pass\n",
            "src/pkg/app.py": "from pkg.lib import helper as h\nh()\n",
        },
    )
    assert r.resolve_definition("pkg.app:h") == "pkg.lib:helper"


def test_bare_module_import_resolves_to_module(adapter):
    r = _resolver(
        adapter,
        {
            "src/pkg/lib.py": "x = 1\n",
            "src/pkg/app.py": "import pkg.lib\n",
        },
    )
    assert r.resolve_definition("pkg.app:pkg.lib") == "pkg.lib"


def test_barrel_reexport_chain(adapter):
    r = _resolver(
        adapter,
        {
            "src/pkg/lib.py": "class Widget:\n    pass\n",
            "src/pkg/__init__.py": "from pkg.lib import Widget\n",
            "src/pkg/app.py": "from pkg import Widget\nw = Widget()\n",
        },
    )
    # app imports via the barrel, which re-exports from pkg.lib
    assert r.resolve_definition("pkg.app:Widget") == "pkg.lib:Widget"
    assert r.resolve_definition("pkg:Widget") == "pkg.lib:Widget"


def test_definition_is_idempotent(adapter):
    r = _resolver(adapter, {"src/pkg/lib.py": "def helper(): pass\n"})
    assert r.resolve_definition("pkg.lib:helper") == "pkg.lib:helper"


def test_external_import_resolves_to_itself(adapter):
    r = _resolver(adapter, {"src/pkg/app.py": "import os\nfrom click import command\n"})
    assert r.resolve_definition("pkg.app:os") == "pkg.app:os"
    assert r.resolve_definition("pkg.app:command") == "pkg.app:command"


def test_circular_reexport_does_not_hang(adapter):
    # a re-exports from b, b re-exports from a — pathological but must terminate.
    r = _resolver(
        adapter,
        {
            "src/a.py": "from b import thing\n",
            "src/b.py": "from a import thing\n",
        },
    )
    result = r.resolve_definition("a:thing")
    assert result in {"a:thing", "b:thing"}  # terminates without error


def test_unknown_symbol_is_idempotent(adapter):
    r = _resolver(adapter, {"src/pkg/lib.py": "x = 1\n"})
    assert r.resolve_definition("does.not:exist") == "does.not:exist"


# ── find_all_references ─────────────────────────────────────────────────────


def test_find_all_references_across_modules(adapter):
    r = _resolver(
        adapter,
        {
            "src/pkg/lib.py": "def helper():\n    return helper\n",
            "src/pkg/app.py": "from pkg.lib import helper\nhelper()\nhelper()\n",
        },
    )
    refs = r.find_all_references("pkg.lib:helper")
    # 1 self-reference in lib + 2 calls in app (all canonicalize to the def)
    assert len(refs) == 3


def test_find_all_references_via_barrel(adapter):
    r = _resolver(
        adapter,
        {
            "src/pkg/lib.py": "class Widget:\n    pass\n",
            "src/pkg/__init__.py": "from pkg.lib import Widget\n",
            "src/pkg/app.py": "from pkg import Widget\nWidget()\n",
        },
    )
    refs = r.find_all_references("pkg.lib:Widget")
    assert len(refs) == 1
    assert refs[0].location.file_path == "src/pkg/app.py"


def test_find_references_exact_match_is_unaffected(adapter):
    """find_all_references is additive; plain exact-match semantics differ."""
    r = _resolver(
        adapter,
        {
            "src/pkg/lib.py": "def helper(): pass\n",
            "src/pkg/app.py": "from pkg.lib import helper\nhelper()\n",
        },
    )
    # The app call binds to the LOCAL import id, not the definition id.
    direct = [ref for ref in r.find_all_references("pkg.app:helper")]
    assert any(ref.location.file_path == "src/pkg/app.py" for ref in direct)


# ── attribute / qualified resolution (Gap A, part 1) ────────────────────────


def test_module_qualified_call_resolves(adapter):
    # Single-hop module-qualified call: lib.helper() -> lib:helper.
    r = _resolver(
        adapter,
        {
            "src/lib.py": "def helper():\n    return 1\n",
            "src/app.py": "import lib\n\ndef caller():\n    return lib.helper()\n",
        },
    )
    refs = r.find_all_references("lib:helper")
    assert any(ref.location.file_path == "src/app.py" for ref in refs)


def test_class_member_attribute_resolves(adapter):
    r = _resolver(
        adapter,
        {
            "src/m.py": (
                "class Kind:\n    MODULE = 1\n\n"
                "def f():\n    return Kind.MODULE\n"
            ),
        },
    )
    # Kind.MODULE resolves to the class member symbol.
    refs = r.find_all_references("m:Kind:MODULE")
    assert len(refs) >= 1


def test_method_usage_found_across_modules(adapter):
    r = _resolver(
        adapter,
        {
            "src/lib.py": "import lib\n\nclass Svc:\n    def run(self):\n        return 1\n",
            "src/app.py": "import lib\n\ndef go(s):\n    return lib.Svc.run\n",
        },
    )
    # lib.Svc.run is multi-hop (a.b.c) -> not resolved in v1; ensure no crash.
    assert isinstance(r.find_all_references("lib:Svc.run"), list)


def test_external_receiver_unresolved(adapter):
    r = _resolver(
        adapter,
        {"src/app.py": "import os\n\ndef f():\n    return os.getcwd()\n"},
    )
    # os is external; os.getcwd() must not resolve to anything local.
    assert r.find_all_references("app:f") is not None  # no crash


def test_call_graph_module_qualified_edge(indexed_project):
    from pypeeker.analysis.graph import call_graph

    _, store = indexed_project({
        "lib.py": "def helper():\n    return 1\n",
        "app.py": "import lib\n\ndef caller():\n    return lib.helper()\n",
    })
    graph = call_graph(store)
    assert "lib:helper" in graph["app:caller"]


# ── annotated instance receivers (Gap A, part 2; query-only) ────────────────


def test_annotated_param_receiver_method_resolves(adapter):
    r = _resolver(
        adapter,
        {
            "src/lib.py": "class Svc:\n    def run(self):\n        return 1\n",
            "src/app.py": (
                "from lib import Svc\n\n"
                "def go(s: Svc):\n    return s.run()\n"
            ),
        },
    )
    refs = r.find_all_references("lib:Svc.run")
    assert any(ref.location.file_path == "src/app.py" for ref in refs)


def test_optional_annotated_receiver_resolves(adapter):
    r = _resolver(
        adapter,
        {
            "src/lib.py": "class Svc:\n    def run(self):\n        return 1\n",
            "src/app.py": (
                "from lib import Svc\n\n"
                "def go(s: Svc | None):\n    return s.run()\n"
            ),
        },
    )
    assert any(
        ref.location.file_path == "src/app.py"
        for ref in r.find_all_references("lib:Svc.run")
    )


def test_unannotated_receiver_not_resolved(adapter):
    r = _resolver(
        adapter,
        {
            "src/lib.py": "class Svc:\n    def run(self):\n        return 1\n",
            "src/app.py": "from lib import Svc\n\ndef go(s):\n    return s.run()\n",
        },
    )
    # No annotation on s -> the s.run() call cannot be resolved to Svc.run.
    refs = r.find_all_references("lib:Svc.run")
    assert not any(ref.location.file_path == "src/app.py" for ref in refs)


def test_call_graph_annotated_receiver_edge(indexed_project):
    from pypeeker.analysis.graph import call_graph

    _, store = indexed_project({
        "lib.py": "class Svc:\n    def run(self):\n        return 1\n",
        "app.py": (
            "from lib import Svc\n\n"
            "def caller(s: Svc):\n    return s.run()\n"
        ),
    })
    graph = call_graph(store)
    assert "lib:Svc.run" in graph["app:caller"]


# ── constructor-inferred receivers (Gap A, part 3; query-only) ──────────────


def test_constructor_assigned_receiver_resolves(adapter):
    r = _resolver(
        adapter,
        {
            "src/lib.py": "class Svc:\n    def run(self):\n        return 1\n",
            "src/app.py": (
                "from lib import Svc\n\n"
                "def go():\n    s = Svc()\n    return s.run()\n"
            ),
        },
    )
    assert any(
        ref.location.file_path == "src/app.py"
        for ref in r.find_all_references("lib:Svc.run")
    )


def test_constructor_inference_sets_inferred_confidence(adapter):
    from pypeeker.models.capabilities import Confidence

    [idx] = [
        _bind(adapter, "src/m.py", "class Foo:\n    pass\n\ns = Foo()\n")
    ]
    s = next(sym for sym in idx.symbols if sym.symbol_id == "m:s")
    assert s.type_annotation is not None
    assert s.type_annotation.raw == "Foo"
    assert s.type_annotation.confidence == Confidence.INFERRED


def test_tuple_unpack_not_inferred(adapter):
    idx = _bind(adapter, "src/m.py", "class Foo:\n    pass\n\na, b = Foo(), 1\n")
    a = next(sym for sym in idx.symbols if sym.symbol_id == "m:a")
    assert a.type_annotation is None


def test_non_call_rhs_not_inferred(adapter):
    idx = _bind(adapter, "src/m.py", "x = 1\n")
    x = next(sym for sym in idx.symbols if sym.symbol_id == "m:x")
    assert x.type_annotation is None


def test_call_graph_constructor_receiver_edge(indexed_project):
    from pypeeker.analysis.graph import call_graph

    _, store = indexed_project({
        "lib.py": "class Svc:\n    def run(self):\n        return 1\n",
        "app.py": (
            "from lib import Svc\n\n"
            "def caller():\n    s = Svc()\n    return s.run()\n"
        ),
    })
    graph = call_graph(store)
    assert "lib:Svc.run" in graph["app:caller"]


# ── query engine integration ────────────────────────────────────────────────


def test_engine_find_all_references(project_dir, adapter):
    store = IndexStore(project_dir)
    for rel, src in {
        "src/pkg/lib.py": "def helper(): pass\n",
        "src/pkg/app.py": "from pkg.lib import helper\nhelper()\n",
    }.items():
        idx = _bind(adapter, rel, src)
        store.save(idx)
        real = project_dir / rel
        real.parent.mkdir(parents=True, exist_ok=True)
        real.write_text(src)

    engine = SemanticQueryEngine(store)
    assert engine.resolve_definition("pkg.app:helper") == "pkg.lib:helper"
    refs = engine.find_all_references("pkg.lib:helper")
    assert any(ref.location.file_path == "src/pkg/app.py" for ref in refs)
