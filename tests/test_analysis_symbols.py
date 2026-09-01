"""Tests for the shared per-file ``symbol_id -> Symbol`` lookup."""

from __future__ import annotations

from pypeeker.analysis import symbols_by_id
from pypeeker.models import SymbolKind


def _duplicate_source() -> str:
    # ``mod:C.m:x`` is bound twice: VARIABLE in the first ``m``, IMPORT in the
    # second (same-named methods produce colliding symbol ids).
    return (
        "class C:\n"
        "    def m(self):\n"
        "        x = 1\n"
        "        return x\n"
        "    def m(self):\n"
        "        import x\n"
        "        return x\n"
    )


def test_duplicate_ids_resolve_first_wins(bind_source):
    index = bind_source(_duplicate_source(), "mod.py")
    duplicates = [s for s in index.symbols if s.symbol_id == "mod:C.m:x"]
    assert [s.kind for s in duplicates] == [SymbolKind.VARIABLE, SymbolKind.IMPORT]
    assert symbols_by_id(index)["mod:C.m:x"] is duplicates[0]


def test_every_symbol_is_reachable_by_id(bind_source):
    index = bind_source("def f(a):\n    b = a\n    return b\n", "mod.py")
    table = symbols_by_id(index)
    assert set(table) == {s.symbol_id for s in index.symbols}
    assert all(table[s.symbol_id].symbol_id == s.symbol_id for s in index.symbols)


def test_lookup_is_memoized_per_index_instance(bind_source):
    index = bind_source("def f(): pass\n", "mod.py")
    first = symbols_by_id(index)
    assert symbols_by_id(index) is first
    # Rebinding the symbol list invalidates the memo.
    index.symbols = list(index.symbols)
    assert symbols_by_id(index) is not first
    assert symbols_by_id(index) == first
