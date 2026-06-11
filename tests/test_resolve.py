"""Tests for cross-module resolution."""

from __future__ import annotations

from pypeeker.binder.binder import bind
from pypeeker.paths import module_path_from
from pypeeker.models.index import FileIndex
from pypeeker.query.engine import SemanticQueryEngine
from pypeeker.resolve import CrossModuleResolver, ResolutionKind
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


def test_relative_import_resolves_to_definition(adapter):
    # src-layout: the file lives under src/ but module paths are src-stripped.
    # The relative import must land in the module namespace (pkg.lib), not
    # the file-path namespace (src.pkg.lib), or the resolver treats the
    # consumer as external.
    r = _resolver(
        adapter,
        {
            "src/pkg/lib.py": "def helper():\n    pass\n",
            "src/pkg/app.py": "from .lib import helper\nhelper()\n",
        },
    )
    assert r.resolve_definition("pkg.app:helper") == "pkg.lib:helper"


def test_relative_barrel_reexport_chain(adapter):
    # __init__.py barrel written with a relative import: pkg/__init__.py's
    # module_path is pkg itself, so ".lib" must resolve to pkg.lib.
    r = _resolver(
        adapter,
        {
            "src/pkg/lib.py": "class Widget:\n    pass\n",
            "src/pkg/__init__.py": "from .lib import Widget\n",
            "src/pkg/app.py": "from pkg import Widget\nw = Widget()\n",
        },
    )
    assert r.resolve_definition("pkg.app:Widget") == "pkg.lib:Widget"
    assert r.resolve_definition("pkg:Widget") == "pkg.lib:Widget"


def test_find_all_references_via_relative_imports(adapter):
    r = _resolver(
        adapter,
        {
            "src/pkg/lib.py": "class Widget:\n    pass\n",
            "src/pkg/__init__.py": "from .lib import Widget\n",
            "src/pkg/app.py": "from . import lib\nfrom .lib import Widget\nWidget()\n",
        },
    )
    refs = r.find_all_references("pkg.lib:Widget")
    assert any(ref.location.file_path == "src/pkg/app.py" for ref in refs)


def test_multilevel_relative_import_resolves(adapter):
    r = _resolver(
        adapter,
        {
            "src/pkg/other.py": "def thing():\n    pass\n",
            "src/pkg/sub/mod.py": "from ..other import thing\nthing()\n",
        },
    )
    assert r.resolve_definition("pkg.sub.mod:thing") == "pkg.other:thing"


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


# ── instance-attribute inference (self.x = Foo()) ───────────────────────────


def test_instance_attribute_creates_class_member(adapter):
    idx = _bind(
        adapter,
        "src/m.py",
        "class Svc:\n    def __init__(self):\n        self.store = Store()\n",
    )
    members = {s.symbol_id for s in idx.symbols}
    assert "m:Svc:store" in members


def test_self_attribute_field_method_resolves(adapter):
    r = _resolver(
        adapter,
        {
            "src/lib.py": "class Store:\n    def save(self):\n        return 1\n",
            "src/app.py": (
                "from lib import Store\n\n"
                "class Svc:\n"
                "    def __init__(self):\n        self.store = Store()\n"
                "    def go(self):\n        return self.store.save()\n"
            ),
        },
    )
    refs = r.find_all_references("lib:Store.save")
    assert any("app:Svc.go" in ref.in_scope_id for ref in refs)


def test_declared_instance_attribute_resolves(adapter):
    r = _resolver(
        adapter,
        {
            "src/lib.py": "class Store:\n    def save(self):\n        return 1\n",
            "src/app.py": (
                "from lib import Store\n\n"
                "class Svc:\n"
                "    def __init__(self, s):\n        self.store: Store = s\n"
                "    def go(self):\n        return self.store.save()\n"
            ),
        },
    )
    refs = r.find_all_references("lib:Store.save")
    assert any("app:Svc.go" in ref.in_scope_id for ref in refs)


# ── return-type dereference (property chains, function results) ─────────────


def test_property_chain_resolves_via_return_type(adapter):
    r = _resolver(
        adapter,
        {
            "src/m.py": (
                "class Inner:\n    def run(self):\n        return 1\n\n"
                "class Outer:\n"
                "    @property\n"
                "    def inner(self) -> Inner:\n        ...\n"
                "    def go(self):\n        return self.inner.run()\n"
            ),
        },
    )
    refs = r.find_all_references("m:Inner.run")
    assert any("m:Outer.go" in ref.in_scope_id for ref in refs)


def test_function_result_variable_resolves_via_return_type(adapter):
    r = _resolver(
        adapter,
        {
            "src/m.py": (
                "class Res:\n    def to_dict(self):\n        return {}\n\n"
                "def make() -> Res:\n    ...\n\n"
                "def go():\n    x = make()\n    return x.to_dict()\n"
            ),
        },
    )
    refs = r.find_all_references("m:Res.to_dict")
    assert any("m:go" in ref.in_scope_id for ref in refs)


def test_return_type_cycle_terminates(adapter):
    # A function returning its own type name must not loop forever.
    r = _resolver(
        adapter,
        {"src/m.py": "def f() -> f:\n    ...\n\ndef g():\n    x = f()\n    return x.y()\n"},
    )
    assert isinstance(r.find_all_references("m:f"), list)  # no hang/crash


# ── multi-hop receiver chains (hop-capped, query-only) ──────────────────────


def test_self_field_method_resolves(adapter):
    r = _resolver(
        adapter,
        {
            "src/lib.py": "class Inner:\n    def run(self):\n        return 1\n",
            "src/app.py": (
                "from lib import Inner\n\n"
                "class Outer:\n"
                "    inner: Inner\n"
                "    def go(self):\n"
                "        return self.inner.run()\n"
            ),
        },
    )
    refs = r.find_all_references("lib:Inner.run")
    assert any(ref.location.file_path == "src/app.py" for ref in refs)


def test_param_field_method_resolves(adapter):
    r = _resolver(
        adapter,
        {
            "src/lib.py": (
                "class Stack:\n    def push(self):\n        return 1\n\n"
                "class State:\n    stack: Stack\n"
            ),
            "src/app.py": (
                "from lib import State\n\n"
                "def visit(s: State):\n    return s.stack.push()\n"
            ),
        },
    )
    refs = r.find_all_references("lib:Stack.push")
    assert any(ref.location.file_path == "src/app.py" for ref in refs)


def test_chain_over_cap_not_resolved(adapter):
    # a.b.c.d.leaf() -> receiver chain length 4 > cap (3): not resolved.
    r = _resolver(
        adapter,
        {
            "src/lib.py": (
                "class D:\n    def leaf(self):\n        return 1\n\n"
                "class C:\n    d: D\n\n"
                "class B:\n    c: C\n\n"
                "class A:\n    b: B\n"
            ),
            "src/app.py": (
                "from lib import A\n\n"
                "def f(a: A):\n    return a.b.c.d.leaf()\n"
            ),
        },
    )
    refs = r.find_all_references("lib:D.leaf")
    assert not any(ref.location.file_path == "src/app.py" for ref in refs)


def test_call_graph_self_field_edge(indexed_project):
    from pypeeker.analysis.graph import call_graph

    _, store = indexed_project({
        "lib.py": "class Inner:\n    def run(self):\n        return 1\n",
        "app.py": (
            "from lib import Inner\n\n"
            "class Outer:\n"
            "    inner: Inner\n"
            "    def go(self):\n"
            "        return self.inner.run()\n"
        ),
    })
    graph = call_graph(store)
    assert "lib:Inner.run" in graph["app:Outer.go"]


# ── find_all_references_classified ──────────────────────────────────────────


def _vias(classified, file_path):
    return {c.via for c in classified if c.reference.location.file_path == file_path}


def test_classified_direct_reference(adapter):
    r = _resolver(
        adapter, {"src/pkg/lib.py": "def helper():\n    pass\n\nhelper()\n"}
    )
    classified = r.find_all_references_classified("pkg.lib:helper")
    assert classified
    assert all(c.via is ResolutionKind.DIRECT for c in classified)


def test_classified_import_alias_reference(adapter):
    r = _resolver(
        adapter,
        {
            "src/pkg/lib.py": "def helper():\n    pass\n",
            "src/pkg/app.py": "from pkg.lib import helper\nhelper()\n",
        },
    )
    classified = r.find_all_references_classified("pkg.lib:helper")
    assert _vias(classified, "src/pkg/app.py") == {ResolutionKind.IMPORT_ALIAS}


def test_classified_barrel_reference(adapter):
    r = _resolver(
        adapter,
        {
            "src/pkg/lib.py": "class Widget:\n    pass\n",
            "src/pkg/__init__.py": "from pkg.lib import Widget\n",
            "src/pkg/app.py": "from pkg import Widget\nWidget()\n",
        },
    )
    classified = r.find_all_references_classified("pkg.lib:Widget")
    assert _vias(classified, "src/pkg/app.py") == {ResolutionKind.BARREL}


def test_classified_receiver_declared(adapter):
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
    classified = r.find_all_references_classified("lib:Svc.run")
    assert _vias(classified, "src/app.py") == {ResolutionKind.RECEIVER_DECLARED}


def test_classified_receiver_inferred(adapter):
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
    classified = r.find_all_references_classified("lib:Svc.run")
    assert _vias(classified, "src/app.py") == {ResolutionKind.RECEIVER_INFERRED}


def test_classified_same_class_self_call_is_direct(adapter):
    # self.run() inside the class is bound by the binder itself (no receiver
    # walk needed), so it classifies as a direct match.
    r = _resolver(
        adapter,
        {
            "src/lib.py": (
                "class Svc:\n"
                "    def run(self):\n        return 1\n"
                "    def go(self):\n        return self.run()\n"
            ),
        },
    )
    classified = r.find_all_references_classified("lib:Svc.run")
    assert _vias(classified, "src/lib.py") == {ResolutionKind.DIRECT}


def test_classified_declared_field_via_self_is_declared(adapter):
    # self.inner.run() through a class-level declared annotation: the receiver
    # walk uses self -> enclosing class -> declared field type, no inference.
    r = _resolver(
        adapter,
        {
            "src/lib.py": "class Inner:\n    def run(self):\n        return 1\n",
            "src/app.py": (
                "from lib import Inner\n\n"
                "class Outer:\n"
                "    inner: Inner\n"
                "    def go(self):\n"
                "        return self.inner.run()\n"
            ),
        },
    )
    classified = r.find_all_references_classified("lib:Inner.run")
    assert _vias(classified, "src/app.py") == {ResolutionKind.RECEIVER_DECLARED}


def test_classified_inferred_instance_attribute(adapter):
    # self.store = Store() is constructor-inferred, so self.store.save() is
    # a receiver_inferred match even though the root is self.
    r = _resolver(
        adapter,
        {
            "src/lib.py": "class Store:\n    def save(self):\n        return 1\n",
            "src/app.py": (
                "from lib import Store\n\n"
                "class Svc:\n"
                "    def __init__(self):\n        self.store = Store()\n"
                "    def go(self):\n        return self.store.save()\n"
            ),
        },
    )
    classified = r.find_all_references_classified("lib:Store.save")
    assert _vias(classified, "src/app.py") == {ResolutionKind.RECEIVER_INFERRED}


def test_declared_only_filters_inferred_via_classification(adapter):
    # find_all_references(declared_only=True) is a filter over the classified
    # results: receiver_inferred matches drop out, everything else stays.
    r = _resolver(
        adapter,
        {
            "src/lib.py": "class Svc:\n    def run(self):\n        return 1\n",
            "src/app.py": (
                "from lib import Svc\n\n"
                "def declared(s: Svc):\n    return s.run()\n\n"
                "def inferred():\n    s = Svc()\n    return s.run()\n"
            ),
        },
    )
    all_refs = r.find_all_references("lib:Svc.run")
    declared_refs = r.find_all_references("lib:Svc.run", declared_only=True)
    assert any("app:inferred" in ref.in_scope_id for ref in all_refs)
    assert not any("app:inferred" in ref.in_scope_id for ref in declared_refs)
    assert any("app:declared" in ref.in_scope_id for ref in declared_refs)


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


def test_engine_find_all_references_classified(project_dir, adapter):
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
    classified = engine.find_all_references_classified("pkg.lib:helper")
    assert _vias(classified, "src/pkg/app.py") == {ResolutionKind.IMPORT_ALIAS}
    # The classified view carries the same references as the plain one.
    assert [c.reference for c in classified] == engine.find_all_references(
        "pkg.lib:helper"
    )
