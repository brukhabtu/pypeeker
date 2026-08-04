"""Variadic parameters bind whether or not they carry a type annotation.

``*args`` and ``**kwargs`` have always bound; ``*args: T`` and ``**kwargs: T``
did not, because tree-sitter nests the name inside a ``list_splat_pattern`` /
``dictionary_splat_pattern`` one level below the ``typed_parameter`` the
binder scanned. The symbol was never declared, so every use of the parameter
in the body came out as an unresolved reference — a false positive from the
gated ``no-unresolved-refs`` rule for any project that annotates its varargs.
"""

from __future__ import annotations

from pypeeker.adapters import PythonAdapter
from pypeeker.binder import bind
from pypeeker.models import SymbolKind

TYPED = """\
def meet(*levels: int, **options: str) -> int:
    return len(levels) + len(options)
"""

UNTYPED = """\
def meet(*levels, **options):
    return len(levels) + len(options)
"""


def _index(source: str):
    adapter = PythonAdapter()
    raw = source.encode("utf-8")
    return bind(adapter, "m.py", raw, adapter.parse(raw).root_node)


def _parameters(index) -> list[str]:
    return [s.symbol_id for s in index.symbols if s.kind is SymbolKind.PARAMETER]


def test_typed_variadic_parameters_are_declared():
    assert _parameters(_index(TYPED)) == ["m:meet:levels", "m:meet:options"]


def test_typed_variadic_parameters_produce_no_unresolved_references():
    index = _index(TYPED)
    assert [r.symbol_id for r in index.references if not r.resolved] == []


def test_typed_and_untyped_variadics_bind_the_same_symbols():
    assert _parameters(_index(TYPED)) == _parameters(_index(UNTYPED))


def test_the_annotation_is_recorded_on_the_typed_variadic():
    by_id = {s.symbol_id: s for s in _index(TYPED).symbols}
    assert by_id["m:meet:levels"].type_annotation.raw == "int"
    assert by_id["m:meet:options"].type_annotation.raw == "str"
