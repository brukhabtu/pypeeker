"""Tests for the binder."""

import pytest

from pypeeker.models.references import ReferenceKind
from pypeeker.models.scopes import ScopeKind
from pypeeker.models.symbols import SymbolKind, Visibility

pytestmark = pytest.mark.integration


class TestSimpleFunction:
    def test_function_symbol(self, bind_source):
        index = bind_source("def greet(name: str) -> str:\n    return name\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test.py:greet" in symbols
        assert symbols["test.py:greet"].kind == SymbolKind.FUNCTION

    def test_function_return_type(self, bind_source):
        index = bind_source("def greet(name: str) -> str:\n    return name\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        func = symbols["test.py:greet"]
        assert func.type_annotation is not None
        assert func.type_annotation.raw == "str"

    def test_parameter_extraction(self, bind_source):
        index = bind_source("def greet(name: str) -> str:\n    return name\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test.py:greet:name" in symbols
        param = symbols["test.py:greet:name"]
        assert param.kind == SymbolKind.PARAMETER
        assert param.type_annotation is not None
        assert param.type_annotation.raw == "str"

    def test_function_scope_created(self, bind_source):
        index = bind_source("def greet(name: str) -> str:\n    return name\n")
        scopes = {s.scope_id: s for s in index.scopes}
        assert "test.py:greet" in scopes
        assert scopes["test.py:greet"].kind == ScopeKind.FUNCTION

    def test_parameter_reference(self, bind_source):
        index = bind_source("def greet(name: str) -> str:\n    return name\n")
        refs = [r for r in index.references if r.symbol_id == "test.py:greet:name"]
        assert len(refs) >= 1
        assert any(r.resolved for r in refs)


class TestClassDefinition:
    def test_class_symbol(self, bind_source):
        index = bind_source("class Foo:\n    pass\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test.py:Foo" in symbols
        assert symbols["test.py:Foo"].kind == SymbolKind.CLASS

    def test_method_symbol(self, bind_source):
        index = bind_source("class Foo:\n    def bar(self):\n        pass\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test.py:Foo.bar" in symbols
        assert symbols["test.py:Foo.bar"].kind == SymbolKind.METHOD

    def test_class_scope(self, bind_source):
        index = bind_source("class Foo:\n    x = 1\n")
        scopes = {s.scope_id: s for s in index.scopes}
        assert "test.py:Foo" in scopes
        assert scopes["test.py:Foo"].kind == ScopeKind.CLASS

    def test_class_variable(self, bind_source):
        index = bind_source("class Foo:\n    x = 1\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test.py:Foo:x" in symbols
        assert symbols["test.py:Foo:x"].kind == SymbolKind.VARIABLE

    def test_docstring(self, bind_source):
        index = bind_source('class Foo:\n    """A class."""\n    pass\n')
        symbols = {s.symbol_id: s for s in index.symbols}
        assert symbols["test.py:Foo"].docstring == "A class."

    def test_nested_class_scope(self, bind_fixture):
        index = bind_fixture("class_with_methods.py")
        scopes = {s.scope_id: s for s in index.scopes}
        assert "class_with_methods.py:Animal" in scopes
        assert "class_with_methods.py:Animal.__init__" in scopes
        assert "class_with_methods.py:Animal.speak" in scopes
        assert "class_with_methods.py:Dog" in scopes


class TestAssignment:
    def test_simple_variable(self, bind_source):
        index = bind_source("x = 1\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test.py:x" in symbols
        assert symbols["test.py:x"].kind == SymbolKind.VARIABLE

    def test_typed_variable(self, bind_source):
        index = bind_source("x: int = 1\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test.py:x" in symbols
        assert symbols["test.py:x"].type_annotation is not None
        assert symbols["test.py:x"].type_annotation.raw == "int"

    def test_tuple_unpacking(self, bind_source):
        index = bind_source("a, b = 1, 2\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test.py:a" in symbols
        assert "test.py:b" in symbols


class TestShadowing:
    def test_shadowing_suffix(self, bind_source):
        index = bind_source("x = 1\nx = 2\nx = 3\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test.py:x" in symbols
        assert "test.py:x$2" in symbols
        assert "test.py:x$3" in symbols

    def test_shadowing_in_function(self, bind_fixture):
        index = bind_fixture("shadowing.py")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "shadowing.py:x" in symbols
        assert "shadowing.py:x$2" in symbols
        assert "shadowing.py:x$3" in symbols
        assert "shadowing.py:process:data" in symbols
        assert "shadowing.py:process:data$2" in symbols
        assert "shadowing.py:process:data$3" in symbols


class TestImports:
    def test_simple_import(self, bind_source):
        index = bind_source("import os\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test.py:os" in symbols
        assert symbols["test.py:os"].kind == SymbolKind.IMPORT

    def test_import_alias(self, bind_source):
        index = bind_source("import sys as system\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test.py:system" in symbols

    def test_from_import(self, bind_source):
        index = bind_source("from pathlib import Path\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test.py:Path" in symbols

    def test_fixture_imports(self, bind_fixture):
        index = bind_fixture("imports_example.py")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "imports_example.py:os" in symbols
        assert "imports_example.py:system" in symbols
        assert "imports_example.py:Path" in symbols
        assert "imports_example.py:OD" in symbols


class TestNestedScopes:
    def test_nested_function(self, bind_fixture):
        index = bind_fixture("nested_scopes.py")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "nested_scopes.py:outer" in symbols
        assert "nested_scopes.py:outer.inner" in symbols

    def test_module_variable(self, bind_fixture):
        index = bind_fixture("nested_scopes.py")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "nested_scopes.py:x" in symbols

    def test_class_and_method(self, bind_fixture):
        index = bind_fixture("nested_scopes.py")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "nested_scopes.py:MyClass" in symbols
        assert "nested_scopes.py:MyClass.method" in symbols
        assert "nested_scopes.py:MyClass:class_var" in symbols

    def test_reference_to_module_var(self, bind_fixture):
        index = bind_fixture("nested_scopes.py")
        # x is referenced in method body
        x_refs = [r for r in index.references if r.symbol_id == "nested_scopes.py:x"]
        assert len(x_refs) >= 1


class TestGlobalNonlocal:
    def test_global_declaration(self, bind_source):
        source = "x = 0\ndef inc():\n    global x\n    x = x + 1\n"
        index = bind_source(source)
        symbols = {s.symbol_id: s for s in index.symbols}
        # The assignment to x inside inc() should create a symbol in module scope
        assert "test.py:x" in symbols


class TestComprehensions:
    def test_list_comprehension_scope(self, bind_fixture):
        index = bind_fixture("comprehensions.py")
        # Comprehensions should create their own scope
        comp_scopes = [s for s in index.scopes if s.kind == ScopeKind.COMPREHENSION]
        assert len(comp_scopes) >= 1

    def test_comprehension_variable(self, bind_fixture):
        index = bind_fixture("comprehensions.py")
        # The module-level variables should be declared
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "comprehensions.py:numbers" in symbols
        assert "comprehensions.py:squares" in symbols


class TestDecorators:
    def test_decorated_function(self, bind_fixture):
        index = bind_fixture("decorators.py")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "decorators.py:decorated_function" in symbols
        func = symbols["decorators.py:decorated_function"]
        assert "my_decorator" in func.decorators

    def test_class_decorators(self, bind_fixture):
        index = bind_fixture("decorators.py")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "decorators.py:MyClass.static_method" in symbols
        static = symbols["decorators.py:MyClass.static_method"]
        assert "staticmethod" in static.decorators


class TestVisibility:
    def test_public(self, bind_source):
        index = bind_source("def foo(): pass\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert symbols["test.py:foo"].visibility == Visibility.PUBLIC

    def test_protected(self, bind_source):
        index = bind_source("def _foo(): pass\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert symbols["test.py:_foo"].visibility == Visibility.PROTECTED

    def test_private(self, bind_source):
        index = bind_source("class C:\n    def __secret(self): pass\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert symbols["test.py:C.__secret"].visibility == Visibility.PRIVATE

    def test_dunder(self, bind_source):
        index = bind_source("class C:\n    def __init__(self): pass\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert symbols["test.py:C.__init__"].visibility == Visibility.DUNDER


class TestReferences:
    def test_function_call_reference(self, bind_source):
        index = bind_source("def foo(): pass\nfoo()\n")
        call_refs = [
            r for r in index.references if r.kind == ReferenceKind.CALL and r.resolved
        ]
        assert any(r.symbol_id == "test.py:foo" for r in call_refs)

    def test_augmented_assignment(self, bind_source):
        index = bind_source("x = 0\nx += 1\n")
        write_refs = [
            r for r in index.references if r.kind == ReferenceKind.WRITE
        ]
        assert len(write_refs) >= 1

    def test_unresolved_reference(self, bind_source):
        index = bind_source("print('hello')\n")
        unresolved = [r for r in index.references if not r.resolved]
        assert any(r.symbol_id == "print" for r in unresolved)


class TestWithStatement:
    def test_with_as_variable(self, bind_source):
        source = 'with open("f.txt") as f:\n    data = f.read()\n'
        index = bind_source(source)
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test.py:f" in symbols
        assert symbols["test.py:f"].kind == SymbolKind.VARIABLE

    def test_with_reference_resolves(self, bind_source):
        source = 'with open("f.txt") as f:\n    data = f.read()\n'
        index = bind_source(source)
        f_refs = [r for r in index.references if r.symbol_id == "test.py:f"]
        assert len(f_refs) >= 1
        assert all(r.resolved for r in f_refs)


class TestExceptClause:
    def test_except_as_variable(self, bind_source):
        source = 'try:\n    pass\nexcept ValueError as e:\n    print(e)\n'
        index = bind_source(source)
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test.py:e" in symbols
        assert symbols["test.py:e"].kind == SymbolKind.VARIABLE

    def test_except_reference_resolves(self, bind_source):
        source = 'try:\n    pass\nexcept ValueError as e:\n    print(e)\n'
        index = bind_source(source)
        e_refs = [r for r in index.references if r.symbol_id == "test.py:e"]
        assert len(e_refs) >= 1
        assert all(r.resolved for r in e_refs)


class TestLambda:
    def test_lambda_scope(self, bind_source):
        index = bind_source("f = lambda x, y: x + y\n")
        lambda_scopes = [s for s in index.scopes if s.kind == ScopeKind.LAMBDA]
        assert len(lambda_scopes) == 1

    def test_lambda_parameters(self, bind_source):
        index = bind_source("f = lambda x, y: x + y\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        param_symbols = [s for s in index.symbols if s.kind == SymbolKind.PARAMETER]
        assert len(param_symbols) == 2
        names = {s.name for s in param_symbols}
        assert names == {"x", "y"}


class TestStarredAssignment:
    def test_starred_unpacking(self, bind_source):
        index = bind_source("a, *b, c = [1, 2, 3, 4, 5]\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test.py:a" in symbols
        assert "test.py:b" in symbols
        assert "test.py:c" in symbols


class TestWalrusOperator:
    def test_walrus_in_if(self, bind_source):
        source = 'if (n := 10) > 5:\n    print(n)\n'
        index = bind_source(source)
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test.py:n" in symbols

    def test_walrus_in_comprehension_binds_to_enclosing(self, bind_source):
        source = "results = [y := x**2 for x in range(5)]\n"
        index = bind_source(source)
        # y should be in module scope, not comprehension scope
        module_scope = [s for s in index.scopes if s.kind == ScopeKind.MODULE][0]
        assert any("y" in sid for sid in module_scope.symbol_ids)


class TestTypeAnnotations:
    def test_function_param_annotation(self, bind_fixture):
        index = bind_fixture("type_annotations.py")
        symbols = {s.symbol_id: s for s in index.symbols}
        data_param = symbols.get("type_annotations.py:process:data")
        assert data_param is not None
        assert data_param.type_annotation is not None
        assert data_param.type_annotation.raw == "list[int]"

    def test_variable_annotation(self, bind_fixture):
        index = bind_fixture("type_annotations.py")
        symbols = {s.symbol_id: s for s in index.symbols}
        count = symbols.get("type_annotations.py:count")
        assert count is not None
        assert count.type_annotation is not None
        assert count.type_annotation.raw == "int"


class TestErrorResilience:
    def test_empty_source(self, bind_source):
        index = bind_source("")
        assert len(index.symbols) == 0
        assert len(index.scopes) == 1  # module scope always exists

    def test_syntax_error_partial_parse(self, bind_source):
        # tree-sitter does best-effort parsing even with errors
        source = "def foo(\n    x = 1\n"
        index = bind_source(source)
        # Should not crash — tree-sitter produces a tree with ERROR nodes
        assert index is not None

    def test_only_comments(self, bind_source):
        index = bind_source("# just a comment\n# another one\n")
        assert len(index.symbols) == 0
        assert len(index.scopes) == 1

    def test_deeply_nested(self, bind_source):
        source = "class A:\n  class B:\n    class C:\n      def d(self):\n        x = 1\n"
        index = bind_source(source)
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test.py:A.B.C.d:x" in symbols

    def test_many_parameters(self, bind_source):
        params = ", ".join(f"p{i}" for i in range(20))
        source = f"def f({params}): pass\n"
        index = bind_source(source)
        param_symbols = [s for s in index.symbols if s.kind == SymbolKind.PARAMETER]
        assert len(param_symbols) == 20


class TestAttributeReferences:
    def test_self_method_call_resolved(self, bind_source):
        source = """
class Foo:
    def bar(self):
        pass
    def baz(self):
        self.bar()
"""
        index = bind_source(source)
        refs = [r for r in index.references if r.symbol_id == "test.py:Foo.bar"]
        call_refs = [r for r in refs if r.kind == ReferenceKind.CALL]
        assert len(call_refs) >= 1
        assert all(r.resolved for r in call_refs)
        assert any(r.is_attribute_access for r in call_refs)

    def test_self_attribute_read_resolved(self, bind_source):
        source = """
class Foo:
    x = 1
    def bar(self):
        return self.x
"""
        index = bind_source(source)
        refs = [r for r in index.references if r.symbol_id == "test.py:Foo:x"]
        read_refs = [r for r in refs if r.kind == ReferenceKind.READ]
        assert len(read_refs) >= 1
        assert all(r.is_attribute_access for r in read_refs)

    def test_self_attribute_write(self, bind_source):
        source = """
class Foo:
    x = 1
    def bar(self):
        self.x = 2
"""
        index = bind_source(source)
        refs = [r for r in index.references if r.symbol_id == "test.py:Foo:x"]
        write_refs = [r for r in refs if r.kind == ReferenceKind.WRITE]
        assert len(write_refs) >= 1
        assert all(r.is_attribute_access for r in write_refs)

    def test_self_unresolved_method(self, bind_source):
        source = """
class Foo:
    def bar(self):
        self.unknown()
"""
        index = bind_source(source)
        unresolved = [r for r in index.references if not r.resolved]
        assert any(r.symbol_id == "<unresolved>.unknown" for r in unresolved)

    def test_obj_method_call_unresolved(self, bind_source):
        source = """
def foo(obj):
    obj.method()
"""
        index = bind_source(source)
        unresolved = [r for r in index.references if not r.resolved]
        assert any(r.symbol_id == "<unresolved>.method" for r in unresolved)

    def test_cls_method_call_resolved(self, bind_source):
        source = """
class Foo:
    @classmethod
    def bar(cls):
        pass
    @classmethod
    def baz(cls):
        cls.bar()
"""
        index = bind_source(source)
        refs = [r for r in index.references if r.symbol_id == "test.py:Foo.bar"]
        call_refs = [r for r in refs if r.kind == ReferenceKind.CALL]
        assert len(call_refs) >= 1
        assert all(r.resolved for r in call_refs)

    def test_chained_attribute_call(self, bind_source):
        source = """
def foo():
    obj.a.b.method()
"""
        index = bind_source(source)
        # Verify it doesn't crash and creates unresolved references
        unresolved = [r for r in index.references if not r.resolved]
        assert any("<unresolved>" in r.symbol_id for r in unresolved)

    def test_self_reference_created_for_object(self, bind_source):
        source = """
class Foo:
    def bar(self):
        self.baz()
"""
        index = bind_source(source)
        # self should have a READ reference
        self_refs = [r for r in index.references if r.symbol_id == "test.py:Foo.bar:self"]
        assert len(self_refs) >= 1
        assert any(r.kind == ReferenceKind.READ for r in self_refs)
