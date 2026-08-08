"""PEP 695 inline type parameters bind in the defining scope.

``def f[T](...)``, ``class C[T]``, and ``type X[T] = ...`` all introduce a
type parameter that tree-sitter-python exposes via a ``type_parameters``
field, but the binder never walked it — so every reference to ``T`` inside
the signature or body came out as an unresolved reference, and pypeeker's
own gated ``no-unresolved-refs`` rule fired on ordinary generic code. This
was worked around in ``dsl/corpus.py`` by spelling ``memo`` with a
module-level ``TypeVar`` instead of PEP 695 syntax; TASK-159 fixes the
binder and restores ``def memo[T](...)`` there as the live proof.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from pypeeker.adapters import PythonAdapter
from pypeeker.analysis import BaseRef, Hierarchy
from pypeeker.binder import bind
from pypeeker.cli import main
from pypeeker.models import ScopeKind, SymbolKind
from pypeeker.query.engine import SemanticQueryEngine


def _index(source: str):
    adapter = PythonAdapter()
    raw = source.encode("utf-8")
    return bind(adapter, "m.py", raw, adapter.parse(raw).root_node)


def _unresolved(index) -> list[str]:
    return [r.symbol_id for r in index.references if not r.resolved]


def _symbol_kinds(index) -> dict[str, SymbolKind]:
    return {s.symbol_id: s.kind for s in index.symbols}


MODULE_FUNCTION = """\
def memo(key, build) -> T:
    x: T = build()
    return x

def memo2[T](key, build: T) -> T:
    x: list[T] = [build()]
    return x
"""


def test_module_function_type_parameter_is_declared_as_type_parameter_kind():
    kinds = _symbol_kinds(_index(MODULE_FUNCTION))
    assert kinds["m:memo2:T"] is SymbolKind.TYPE_PARAMETER


def test_module_function_type_parameter_resolves_return_parameter_and_body_refs():
    index = _index(MODULE_FUNCTION)
    # memo2's `T` covers the return annotation, a parameter annotation, a
    # nested-generic body annotation (list[T]), and the subscript itself —
    # none of memo2's references to `T` are unresolved.
    unresolved_scopes = {
        r.in_scope_id for r in index.references if r.symbol_id == "T" and not r.resolved
    }
    assert "m:memo2" not in unresolved_scopes


def test_module_function_without_type_parameters_still_leaves_bare_t_unresolved():
    # Regression guard: memo (no `[T]`) must NOT pick up memo2's binding —
    # its `T` references (return annotation in module scope, body annotation
    # in its own scope) stay ordinary unresolved references, proving the
    # type parameter is scoped to memo2 alone, not leaked to the module.
    index = _index(MODULE_FUNCTION)
    unresolved = [r for r in index.references if r.symbol_id == "T" and not r.resolved]
    assert {r.in_scope_id for r in unresolved} == {"m", "m:memo"}


METHOD_MEMO = """\
from collections.abc import Callable, Hashable

class Corpus:
    def memo[T](self, key: Hashable, build: Callable[[], T]) -> T:
        return build()
"""


def test_previously_failing_memo_method_shape_has_no_unresolved_references():
    index = _index(METHOD_MEMO)
    assert _unresolved(index) == []


def test_previously_failing_memo_method_shape_declares_type_parameter_symbol():
    kinds = _symbol_kinds(_index(METHOD_MEMO))
    assert kinds["m:Corpus.memo:T"] is SymbolKind.TYPE_PARAMETER


GENERIC_CLASS = """\
class Box[T](list[T]):
    field: T

    def get(self) -> T:
        y: T = self.field
        return y
"""


def test_generic_class_type_parameter_is_declared_on_the_class_scope():
    kinds = _symbol_kinds(_index(GENERIC_CLASS))
    assert kinds["m:Box:T"] is SymbolKind.TYPE_PARAMETER


def test_generic_class_type_parameter_resolves_everywhere_it_is_used():
    # Covers the base-class subscript, a class-body annotation, a method
    # return annotation, and a method-body annotation — the last two are
    # the regression case for the ScopeStack.resolve class-scope exception,
    # since a class scope is otherwise invisible to a nested method.
    index = _index(GENERIC_CLASS)
    assert _unresolved(index) == []


GENERIC_ALIAS = "type Alias[T] = list[T]\n"
PLAIN_ALIAS = "type Alias = list[int]\n"


def test_generic_type_alias_declares_variable_and_type_parameter_symbols():
    kinds = _symbol_kinds(_index(GENERIC_ALIAS))
    assert kinds["m:Alias"] is SymbolKind.VARIABLE
    assert kinds["m:Alias:T"] is SymbolKind.TYPE_PARAMETER


def test_generic_type_alias_opens_a_type_params_scope():
    index = _index(GENERIC_ALIAS)
    scope_kinds = {s.scope_id: s.kind for s in index.scopes}
    assert scope_kinds["m:Alias"] is ScopeKind.TYPE_PARAMS


def test_generic_type_alias_has_no_unresolved_references():
    assert _unresolved(_index(GENERIC_ALIAS)) == []


def test_plain_type_alias_declares_the_alias_name_and_opens_no_type_params_scope():
    index = _index(PLAIN_ALIAS)
    kinds = _symbol_kinds(index)
    assert kinds["m:Alias"] is SymbolKind.VARIABLE
    assert all(s.kind is not ScopeKind.TYPE_PARAMS for s in index.scopes)


def test_plain_type_alias_has_no_unresolved_references():
    assert _unresolved(_index(PLAIN_ALIAS)) == []


BOUNDED_AND_CONSTRAINED = "def b[T: int, U: (str, bytes)](a: T) -> U: ...\n"
SPLAT_FORMS = "def v[*Ts, **P](a) -> None: ...\n"


def test_bounded_and_constrained_type_parameters_are_declared_and_resolve():
    index = _index(BOUNDED_AND_CONSTRAINED)
    kinds = _symbol_kinds(index)
    assert kinds["m:b:T"] is SymbolKind.TYPE_PARAMETER
    assert kinds["m:b:U"] is SymbolKind.TYPE_PARAMETER
    assert _unresolved(index) == []


def test_splat_and_double_splat_type_parameters_are_declared_and_resolve():
    index = _index(SPLAT_FORMS)
    kinds = _symbol_kinds(index)
    assert kinds["m:v:Ts"] is SymbolKind.TYPE_PARAMETER
    assert kinds["m:v:P"] is SymbolKind.TYPE_PARAMETER
    assert _unresolved(index) == []


PEP696_DEFAULT = "def d[T = int](a: T) -> T: ...\n"


def test_pep696_default_syntax_is_not_bound_as_a_type_parameter():
    # The pinned tree-sitter-python grammar cannot parse `[T = int]`; it
    # surfaces as an ERROR node. The has_error guard must decline to bind
    # anything out of it rather than invent a `int`-named type parameter
    # from the surviving fragments.
    index = _index(PEP696_DEFAULT)
    kinds = _symbol_kinds(index)
    assert all(kind is not SymbolKind.TYPE_PARAMETER for kind in kinds.values())
    assert "int" not in kinds


def test_pep696_default_syntax_still_records_a_syntax_error():
    index = _index(PEP696_DEFAULT)
    assert index.errors != []


NON_GENERIC_FUNCTION = "def plain(a: int) -> int:\n    return a\n"


def test_non_generic_return_annotation_still_binds_in_the_module_scope():
    # Pins that the return-annotation reorder in visit_function_definition
    # is conditional on a `type_parameters` field being present: a plain
    # function's return annotation is still evaluated (and its reference
    # still recorded as in-scope) in the enclosing module scope.
    index = _index(NON_GENERIC_FUNCTION)
    int_refs = {r.in_scope_id for r in index.references if r.symbol_id == "<builtins>.int"}
    # The return annotation (`-> int`) is evaluated in the enclosing module
    # scope; the parameter annotation (`a: int`) is evaluated inside the
    # function's own scope — both must still hold true for a non-generic
    # function, since the reorder in visit_function_definition only applies
    # when a `type_parameters` field is present.
    assert int_refs == {"m", "m:plain"}


# --- The PEP 695 annotation scope sits *outside* the definition ------------
#
# PEP 695: "If an annotation scope is immediately within a class scope ... the
# code in the annotation scope can use names defined in the class scope."
# Binding a generic definition's header (bases / return annotation / alias
# value) *inside* the definition's own scope would hide that class namespace,
# because ScopeStack.resolve skips class scopes on the way out — turning code
# CPython resolves into fresh no-unresolved-refs findings. So the header is
# bound one scope out, with the type parameters overlaid.

GENERIC_HEADERS_IN_A_CLASS_BODY = """\
class Base0:
    pass

class Holder:
    Alias = int
    Base = Base0
    Elem = int

    def convert[T](self, v: T) -> Alias:
        return v

    class Inner[T](Base):
        pass

    type A[T] = list[Elem]
"""


def test_generic_headers_inside_a_class_body_see_the_class_namespace():
    # Verified against CPython 3.14: get_annotations(Holder.convert) is
    # {'return': int}, Holder.Inner.__mro__ contains Base0, and
    # Holder.A.__value__ is list[int].
    assert _unresolved(_index(GENERIC_HEADERS_IN_A_CLASS_BODY)) == []


def test_generic_headers_inside_a_class_body_bind_in_the_class_scope():
    # Not just "resolved" — the reference must be recorded *in the class
    # scope*, the same place a non-generic definition's header lands, so
    # `pypeeker refs` on a class attribute still reports these use sites.
    index = _index(GENERIC_HEADERS_IN_A_CLASS_BODY)
    header_refs = {
        r.symbol_id: r.in_scope_id
        for r in index.references
        if r.symbol_id in ("m:Holder:Alias", "m:Holder:Base", "m:Holder:Elem")
    }
    assert header_refs == {
        "m:Holder:Alias": "m:Holder",
        "m:Holder:Base": "m:Holder",
        "m:Holder:Elem": "m:Holder",
    }


SHADOWING_HEADER = """\
class Holder:
    T = int

    def convert[T](self, v: T) -> T:
        return v
"""


def test_a_type_parameter_shadows_a_same_named_class_attribute_in_the_header():
    # The annotation scope is *inside* the class scope, so `-> T` is the type
    # parameter, not `Holder.T`.
    index = _index(SHADOWING_HEADER)
    return_refs = [
        r
        for r in index.references
        if r.in_scope_id == "m:Holder" and r.symbol_id in ("m:Holder:T", "m:Holder.convert:T")
    ]
    assert [r.symbol_id for r in return_refs] == ["m:Holder.convert:T"]


# --- Base-class references must stay in the class's parent scope -----------
#
# analysis/hierarchy.py discriminates a base-class reference from any other
# reference in a class header with `ref.in_scope_id == parent_scope_id`. If a
# generic class's bases were bound inside its own scope, every PEP 695 generic
# class would silently report zero bases and zero overrides — and with them
# the method-override refusal that guards rename/demote/promote/privatize.

GENERIC_SUBCLASS = """\
class Base:
    def hi(self):
        return 0

class Kid[T](Base):
    def hi(self):
        return 1
"""


def test_generic_class_base_reference_is_recorded_in_the_parent_scope():
    index = _index(GENERIC_SUBCLASS)
    base_scopes = [
        r.in_scope_id for r in index.references if r.symbol_id == "m:Base"
    ]
    assert base_scopes == ["m"]


def test_hierarchy_still_sees_a_generic_class_base_and_its_overrides(indexed_project):
    _, store = indexed_project({"mod.py": GENERIC_SUBCLASS.replace("m:", "mod:")})
    h = Hierarchy.from_store(store)
    assert h.bases("mod:Kid") == [BaseRef(text="Base", class_id="mod:Base")]
    assert h.overrides("mod:Kid.hi") == ["mod:Base.hi"]
    assert h.overridden_by("mod:Base.hi") == ["mod:Kid.hi"]
    assert h.mro_unknown("mod:Kid") is False


# --- `pypeeker scope` must agree with the binder ---------------------------

CLASS_TYPE_PARAM_VISIBILITY = """\
class Gen[T]:
    def meth(self, v: T) -> T:
        return v
"""


def test_scope_query_reports_a_class_type_parameter_as_visible_from_a_method(store):
    adapter = PythonAdapter()
    raw = CLASS_TYPE_PARAM_VISIBILITY.encode("utf-8")
    store.save(bind(adapter, "test.py", raw, adapter.parse(raw).root_node))
    (store.project_root / "test.py").write_bytes(raw)

    engine = SemanticQueryEngine(store)
    # Line 2 is inside `meth`'s body — a nested scope under the class scope
    # that owns `T`. The binder resolves `T` there, so `scope` must list it.
    result = engine.get_scope_at("test.py", 2)
    visible = {s["symbol_id"] for s in result["visible_symbols"]}
    assert "test:Gen:T" in visible
    # ...and the class-scope skip still holds for everything else: `meth`
    # itself is a class-scope symbol and must not leak into the method body.
    assert "test:Gen.meth" not in visible


# --- `global`/`nonlocal` beat the class type parameter ---------------------

GLOBAL_SHADOWS_CLASS_TYPE_PARAM = """\
T = 1


class C[T]:
    def m(self):
        global T
        T = 2
        return T
"""


def test_global_in_a_method_beats_the_enclosing_class_type_parameter():
    # CPython 3.14 accepts this and `C().m()` returns 2: the `global T`
    # declaration rebinds the name in the method, so `return T` reads the
    # module global — not `class C[T]`'s type parameter. The class-scope
    # type-parameter exception in ScopeStack.resolve must yield to it.
    index = _index(GLOBAL_SHADOWS_CLASS_TYPE_PARAM)
    reads = [r for r in index.references if r.kind.value == "read"]
    assert [(r.symbol_id, r.in_scope_id) for r in reads] == [("m:T$2", "m:C.m")]


NESTED_UNDER_GLOBAL = """\
T = 1


class C[T]:
    def m(self):
        global T
        T = 2

        def inner(v: T):
            return v

        return inner
"""


def test_a_function_nested_under_the_global_still_sees_the_type_parameter():
    # The guard is scoped to the *current* scope's own declarations: `inner`
    # has no `global T` of its own, so CPython resolves its annotation to the
    # class type parameter. Widening the guard to every intervening scope
    # would wrongly unresolve this.
    index = _index(NESTED_UNDER_GLOBAL)
    annotations = [
        r for r in index.references if r.kind.value == "type_annotation"
    ]
    assert [(r.symbol_id, r.in_scope_id) for r in annotations] == [
        ("m:C:T", "m:C.m.inner")
    ]


def test_renaming_a_class_type_parameter_leaves_a_global_of_the_same_name_alone(
    tmp_path, monkeypatch
):
    # The binder bug reached bytes: mis-resolving `return T` to the type
    # parameter made `rename` rewrite it, producing a module that raises
    # NameError at run time. Pin the file contents, not just the symbol_id.
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
    (tmp_path / ".pypeeker" / "index").mkdir(parents=True, exist_ok=True)
    source = tmp_path / "mod.py"
    source.write_text(GLOBAL_SHADOWS_CLASS_TYPE_PARAM)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(main, ["index", str(source)], catch_exceptions=False)
    assert result.exit_code == 0

    result = runner.invoke(
        main, ["rename", "mod:C:T", "QQ"], catch_exceptions=False
    )
    assert result.exit_code == 0
    output = json.loads(result.output)
    assert output["applied"] is True
    # Only the declaration itself: the type parameter has no references.
    assert output["edit_count"] == 1

    assert source.read_text() == (
        "T = 1\n"
        "\n"
        "\n"
        "class C[QQ]:\n"
        "    def m(self):\n"
        "        global T\n"
        "        T = 2\n"
        "        return T\n"
    )
