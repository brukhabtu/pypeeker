"""Which binding forms the binder records no symbol for — and which it now does.

``MovedBodyClosed`` (move-symbol) reads "the binder left this bare name
unresolved" as "not bound locally", and patches the premise for the forms the
binder is blind to with ``_binder_blind_bindings``. This file pins the premise
against the *current* binder so the patch's scope tracks the binder: two forms
remain blind (``match``/``case`` captures and an unpacking ``as``-target);
PEP 695 type parameters used to be a third and are now declared as
``TYPE_PARAMETER`` symbols, so the scan no longer covers them.
"""

from __future__ import annotations

from pypeeker.models import SymbolKind
from pypeeker.refactor import cst
from pypeeker.refactor.preconditions.move import _binder_blind_bindings

CASE_CAPTURE = "def f(x):\n    match x:\n        case [a, *rest]:\n            return a.b\n"
AS_UNPACK = "def f(cm):\n    with cm as (a, b):\n        return a.b\n"
TYPE_PARAMETER = "def f[a](value: a) -> a:\n    return a.b\n"
CLASS_TYPE_PARAMETER = "class C[a]:\n    x = a.b\n"
SUBSCRIPT_NOT_A_DECLARATION = "def f(value: list[a]) -> int:\n    return len(value)\n"


def _bare_reads(index, name: str) -> list[bool]:
    """``resolved`` flag of every non-attribute reference spelled ``name``."""
    return [
        ref.resolved
        for ref in index.references
        if ref.symbol_id == name and not ref.is_attribute_access
    ]


class TestBinderPremise:
    """The binder facts the move-symbol scan is built on."""

    def test_a_case_capture_leaves_an_unresolved_read_and_no_symbol(self, bind_source):
        index = bind_source(CASE_CAPTURE)
        assert not any(s.name == "a" for s in index.symbols)
        assert _bare_reads(index, "a") and not any(_bare_reads(index, "a"))

    def test_an_unpacking_as_target_leaves_an_unresolved_read_and_no_symbol(
        self, bind_source
    ):
        index = bind_source(AS_UNPACK)
        assert not any(s.name == "a" for s in index.symbols)
        assert _bare_reads(index, "a") and not any(_bare_reads(index, "a"))

    def test_a_type_parameter_is_declared_and_its_reads_resolve(self, bind_source):
        index = bind_source(TYPE_PARAMETER)
        declared = [s for s in index.symbols if s.name == "a"]
        assert [s.kind for s in declared] == [SymbolKind.TYPE_PARAMETER]
        assert all(
            ref.resolved for ref in index.references if ref.symbol_id == declared[0].symbol_id
        )
        assert not _bare_reads(index, "a")


class TestBinderBlindScan:
    """``_binder_blind_bindings`` covers exactly the forms the binder misses."""

    def test_case_capture_is_recorded_over_its_enclosing_function(self):
        bound = _binder_blind_bindings(cst.parse(CASE_CAPTURE.encode()), 0)
        assert set(bound) == {"a", "rest"}
        ((start, end),) = bound["a"]
        assert start == (0, 0)
        assert end[0] == 3  # the whole ``def f`` is the region

    def test_unpacking_as_target_is_recorded(self):
        bound = _binder_blind_bindings(cst.parse(AS_UNPACK.encode()), 0)
        assert set(bound) == {"a", "b"}

    def test_type_parameters_are_the_binder_s_job_now(self):
        for source in (TYPE_PARAMETER, CLASS_TYPE_PARAMETER):
            assert _binder_blind_bindings(cst.parse(source.encode()), 0) == {}

    def test_a_subscript_in_a_type_position_records_nothing(self):
        bound = _binder_blind_bindings(cst.parse(SUBSCRIPT_NOT_A_DECLARATION.encode()), 0)
        assert bound == {}

    def test_line_offset_shifts_the_recorded_region(self):
        bound = _binder_blind_bindings(cst.parse(CASE_CAPTURE.encode()), 10)
        ((start, _end),) = bound["a"]
        assert start == (10, 0)
