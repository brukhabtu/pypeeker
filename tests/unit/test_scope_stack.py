"""Unit tests for ScopeStack."""

import pytest

from pypeeker.binder.scope_stack import ScopeEntry, ScopeStack
from pypeeker.models.location import Position, Span
from pypeeker.models.scopes import Scope, ScopeKind
from pypeeker.models.symbols import Symbol, SymbolKind, Visibility
from pypeeker.models.capabilities import Confidence

pytestmark = pytest.mark.unit


def _make_scope(scope_id: str, name: str, kind: ScopeKind, parent_id: str | None = None) -> Scope:
    return Scope(
        scope_id=scope_id,
        name=name,
        kind=kind,
        file_path="test.py",
        span=Span(start=Position(line=0, column=0), end=Position(line=10, column=0)),
        parent_scope_id=parent_id,
    )


def _make_symbol(symbol_id: str, name: str, kind: SymbolKind = SymbolKind.VARIABLE) -> Symbol:
    return Symbol(
        symbol_id=symbol_id,
        name=name,
        kind=kind,
        location=_make_location(),
        visibility=Visibility.PUBLIC,
        visibility_confidence=Confidence.HEURISTIC,
        parent_scope_id="test.py",
    )


def _make_location():
    from pypeeker.models.location import Location
    return Location(
        file_path="test.py",
        span=Span(start=Position(line=0, column=0), end=Position(line=0, column=1)),
    )


class TestScopeStackBasics:
    def test_push_and_pop(self):
        stack = ScopeStack()
        scope = _make_scope("test.py", "test.py", ScopeKind.MODULE)
        stack.push(scope)
        assert stack.depth == 1
        assert stack.current_scope is scope
        popped = stack.pop()
        assert popped is scope
        assert stack.depth == 0

    def test_current_entry(self):
        stack = ScopeStack()
        scope = _make_scope("test.py", "test.py", ScopeKind.MODULE)
        stack.push(scope)
        entry = stack.current
        assert isinstance(entry, ScopeEntry)
        assert entry.scope is scope

    def test_module_entry(self):
        stack = ScopeStack()
        module = _make_scope("test.py", "test.py", ScopeKind.MODULE)
        func = _make_scope("test.py:foo", "foo", ScopeKind.FUNCTION, "test.py")
        stack.push(module)
        stack.push(func)
        assert stack.module_entry.scope is module


class TestDeclare:
    def test_first_declaration_no_suffix(self):
        stack = ScopeStack()
        stack.push(_make_scope("test.py", "test.py", ScopeKind.MODULE))
        sym = _make_symbol("test.py:x", "x")
        result = stack.declare("x", sym)
        assert result == "test.py:x"

    def test_shadowing_adds_suffix(self):
        stack = ScopeStack()
        stack.push(_make_scope("test.py", "test.py", ScopeKind.MODULE))
        sym1 = _make_symbol("test.py:x", "x")
        sym2 = _make_symbol("test.py:x", "x")
        sym3 = _make_symbol("test.py:x", "x")
        stack.declare("x", sym1)
        result2 = stack.declare("x", sym2)
        result3 = stack.declare("x", sym3)
        assert result2 == "test.py:x$2"
        assert result3 == "test.py:x$3"

    def test_declare_in_scope(self):
        stack = ScopeStack()
        module = _make_scope("test.py", "test.py", ScopeKind.MODULE)
        func = _make_scope("test.py:foo", "foo", ScopeKind.FUNCTION, "test.py")
        stack.push(module)
        stack.push(func)

        # Declare in module scope while inside function
        sym = _make_symbol("test.py:x", "x")
        result = stack.declare_in_scope("x", sym, stack.module_entry)
        assert result == "test.py:x"
        assert stack.module_entry.lookup_local("x") is sym


class TestResolve:
    def test_resolve_local(self):
        stack = ScopeStack()
        stack.push(_make_scope("test.py", "test.py", ScopeKind.MODULE))
        sym = _make_symbol("test.py:x", "x")
        stack.declare("x", sym)
        assert stack.resolve("x") is sym

    def test_resolve_from_enclosing_scope(self):
        stack = ScopeStack()
        module = _make_scope("test.py", "test.py", ScopeKind.MODULE)
        func = _make_scope("test.py:foo", "foo", ScopeKind.FUNCTION, "test.py")
        stack.push(module)
        sym = _make_symbol("test.py:x", "x")
        stack.declare("x", sym)
        stack.push(func)
        assert stack.resolve("x") is sym

    def test_resolve_skips_class_scope(self):
        stack = ScopeStack()
        module = _make_scope("test.py", "test.py", ScopeKind.MODULE)
        cls = _make_scope("test.py:Foo", "Foo", ScopeKind.CLASS, "test.py")
        method = _make_scope("test.py:Foo.bar", "bar", ScopeKind.FUNCTION, "test.py:Foo")
        stack.push(module)
        stack.push(cls)
        class_var = _make_symbol("test.py:Foo:x", "x")
        stack.declare("x", class_var)
        stack.push(method)
        # Method should NOT resolve class variables via normal lookup
        assert stack.resolve("x") is None

    def test_resolve_finds_module_through_class(self):
        stack = ScopeStack()
        module = _make_scope("test.py", "test.py", ScopeKind.MODULE)
        cls = _make_scope("test.py:Foo", "Foo", ScopeKind.CLASS, "test.py")
        method = _make_scope("test.py:Foo.bar", "bar", ScopeKind.FUNCTION, "test.py:Foo")
        stack.push(module)
        module_var = _make_symbol("test.py:y", "y")
        stack.declare("y", module_var)
        stack.push(cls)
        stack.push(method)
        # Should skip class scope but find module-level variable
        assert stack.resolve("y") is module_var

    def test_resolve_not_found(self):
        stack = ScopeStack()
        stack.push(_make_scope("test.py", "test.py", ScopeKind.MODULE))
        assert stack.resolve("nonexistent") is None


class TestGlobalNonlocal:
    def test_find_global_target(self):
        stack = ScopeStack()
        module = _make_scope("test.py", "test.py", ScopeKind.MODULE)
        func = _make_scope("test.py:foo", "foo", ScopeKind.FUNCTION, "test.py")
        stack.push(module)
        stack.push(func)
        assert stack.find_global_target().scope is module

    def test_find_nonlocal_target(self):
        stack = ScopeStack()
        module = _make_scope("test.py", "test.py", ScopeKind.MODULE)
        outer = _make_scope("test.py:outer", "outer", ScopeKind.FUNCTION, "test.py")
        inner = _make_scope("test.py:outer.inner", "inner", ScopeKind.FUNCTION, "test.py:outer")
        stack.push(module)
        stack.push(outer)
        stack.push(inner)
        target = stack.find_nonlocal_target("x")
        assert target is not None
        assert target.scope is outer

    def test_find_nonlocal_target_no_function(self):
        stack = ScopeStack()
        module = _make_scope("test.py", "test.py", ScopeKind.MODULE)
        stack.push(module)
        assert stack.find_nonlocal_target("x") is None


class TestEnclosingScopes:
    def test_find_enclosing_function(self):
        stack = ScopeStack()
        module = _make_scope("test.py", "test.py", ScopeKind.MODULE)
        func = _make_scope("test.py:foo", "foo", ScopeKind.FUNCTION, "test.py")
        comp = _make_scope("test.py:foo:<comp:5>", "<comprehension>", ScopeKind.COMPREHENSION, "test.py:foo")
        stack.push(module)
        stack.push(func)
        stack.push(comp)
        entry = stack.find_enclosing_function_entry()
        assert entry is not None
        assert entry.scope is func

    def test_find_enclosing_function_fallback_to_module(self):
        stack = ScopeStack()
        module = _make_scope("test.py", "test.py", ScopeKind.MODULE)
        comp = _make_scope("test.py:<comp:0>", "<comprehension>", ScopeKind.COMPREHENSION, "test.py")
        stack.push(module)
        stack.push(comp)
        entry = stack.find_enclosing_function_entry()
        assert entry is not None
        assert entry.scope is module

    def test_find_enclosing_class(self):
        stack = ScopeStack()
        module = _make_scope("test.py", "test.py", ScopeKind.MODULE)
        cls = _make_scope("test.py:Foo", "Foo", ScopeKind.CLASS, "test.py")
        method = _make_scope("test.py:Foo.bar", "bar", ScopeKind.FUNCTION, "test.py:Foo")
        stack.push(module)
        stack.push(cls)
        stack.push(method)
        assert stack.find_enclosing_class() is cls

    def test_find_enclosing_class_none(self):
        stack = ScopeStack()
        module = _make_scope("test.py", "test.py", ScopeKind.MODULE)
        func = _make_scope("test.py:foo", "foo", ScopeKind.FUNCTION, "test.py")
        stack.push(module)
        stack.push(func)
        assert stack.find_enclosing_class() is None

    def test_get_class_scope_entry(self):
        stack = ScopeStack()
        module = _make_scope("test.py", "test.py", ScopeKind.MODULE)
        cls = _make_scope("test.py:Foo", "Foo", ScopeKind.CLASS, "test.py")
        stack.push(module)
        stack.push(cls)
        entry = stack.get_class_scope_entry("test.py:Foo")
        assert entry is not None
        assert entry.scope is cls

    def test_get_class_scope_entry_not_found(self):
        stack = ScopeStack()
        stack.push(_make_scope("test.py", "test.py", ScopeKind.MODULE))
        assert stack.get_class_scope_entry("nonexistent") is None


class TestSymbolIds:
    def test_build_scope_chain_module_only(self):
        stack = ScopeStack()
        stack.push(_make_scope("test.py", "test.py", ScopeKind.MODULE))
        assert stack.build_scope_chain("test.py") == "test.py"

    def test_build_scope_chain_with_function(self):
        stack = ScopeStack()
        stack.push(_make_scope("test.py", "test.py", ScopeKind.MODULE))
        stack.push(_make_scope("test.py:foo", "foo", ScopeKind.FUNCTION, "test.py"))
        assert stack.build_scope_chain("test.py") == "test.py:foo"

    def test_build_scope_chain_nested(self):
        stack = ScopeStack()
        stack.push(_make_scope("test.py", "test.py", ScopeKind.MODULE))
        stack.push(_make_scope("test.py:Foo", "Foo", ScopeKind.CLASS, "test.py"))
        stack.push(_make_scope("test.py:Foo.bar", "bar", ScopeKind.FUNCTION, "test.py:Foo"))
        assert stack.build_scope_chain("test.py") == "test.py:Foo.bar"

    def test_build_symbol_id_scope_creator(self):
        stack = ScopeStack()
        stack.push(_make_scope("test.py", "test.py", ScopeKind.MODULE))
        assert stack.build_symbol_id("test.py", "foo", is_scope_creator=True) == "test.py:foo"

    def test_build_symbol_id_local(self):
        stack = ScopeStack()
        stack.push(_make_scope("test.py", "test.py", ScopeKind.MODULE))
        stack.push(_make_scope("test.py:foo", "foo", ScopeKind.FUNCTION, "test.py"))
        assert stack.build_symbol_id("test.py", "x", is_scope_creator=False) == "test.py:foo:x"

    def test_build_symbol_id_nested_scope_creator(self):
        stack = ScopeStack()
        stack.push(_make_scope("test.py", "test.py", ScopeKind.MODULE))
        stack.push(_make_scope("test.py:Foo", "Foo", ScopeKind.CLASS, "test.py"))
        assert stack.build_symbol_id("test.py", "bar", is_scope_creator=True) == "test.py:Foo.bar"


class TestScopeEntry:
    def test_declaration_count_empty(self):
        entry = ScopeEntry(scope=_make_scope("test.py", "test.py", ScopeKind.MODULE))
        assert entry.declaration_count("x") == 0

    def test_add_and_lookup(self):
        entry = ScopeEntry(scope=_make_scope("test.py", "test.py", ScopeKind.MODULE))
        sym = _make_symbol("test.py:x", "x")
        entry.add_declaration("x", sym)
        assert entry.lookup_local("x") is sym
        assert entry.declaration_count("x") == 1

    def test_lookup_returns_most_recent(self):
        entry = ScopeEntry(scope=_make_scope("test.py", "test.py", ScopeKind.MODULE))
        sym1 = _make_symbol("test.py:x", "x")
        sym2 = _make_symbol("test.py:x$2", "x")
        entry.add_declaration("x", sym1)
        entry.add_declaration("x", sym2)
        assert entry.lookup_local("x") is sym2

    def test_lookup_nonexistent(self):
        entry = ScopeEntry(scope=_make_scope("test.py", "test.py", ScopeKind.MODULE))
        assert entry.lookup_local("nonexistent") is None
