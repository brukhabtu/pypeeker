"""Tests for the binder."""

from pypeeker.models.references import ReferenceKind
from pypeeker.models.scopes import ScopeKind
from pypeeker.models.symbols import SymbolKind, Visibility


class TestSimpleFunction:
    def test_function_symbol(self, bind_source):
        index = bind_source("def greet(name: str) -> str:\n    return name\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test:greet" in symbols
        assert symbols["test:greet"].kind == SymbolKind.FUNCTION

    def test_function_return_type(self, bind_source):
        index = bind_source("def greet(name: str) -> str:\n    return name\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        func = symbols["test:greet"]
        assert func.type_annotation is not None
        assert func.type_annotation.raw == "str"

    def test_parameter_extraction(self, bind_source):
        index = bind_source("def greet(name: str) -> str:\n    return name\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test:greet:name" in symbols
        param = symbols["test:greet:name"]
        assert param.kind == SymbolKind.PARAMETER
        assert param.type_annotation is not None
        assert param.type_annotation.raw == "str"

    def test_function_scope_created(self, bind_source):
        index = bind_source("def greet(name: str) -> str:\n    return name\n")
        scopes = {s.scope_id: s for s in index.scopes}
        assert "test:greet" in scopes
        assert scopes["test:greet"].kind == ScopeKind.FUNCTION

    def test_parameter_reference(self, bind_source):
        index = bind_source("def greet(name: str) -> str:\n    return name\n")
        refs = [r for r in index.references if r.symbol_id == "test:greet:name"]
        assert len(refs) >= 1
        assert any(r.resolved for r in refs)


class TestClassDefinition:
    def test_class_symbol(self, bind_source):
        index = bind_source("class Foo:\n    pass\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test:Foo" in symbols
        assert symbols["test:Foo"].kind == SymbolKind.CLASS

    def test_method_symbol(self, bind_source):
        index = bind_source("class Foo:\n    def bar(self):\n        pass\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test:Foo.bar" in symbols
        assert symbols["test:Foo.bar"].kind == SymbolKind.METHOD

    def test_class_scope(self, bind_source):
        index = bind_source("class Foo:\n    x = 1\n")
        scopes = {s.scope_id: s for s in index.scopes}
        assert "test:Foo" in scopes
        assert scopes["test:Foo"].kind == ScopeKind.CLASS

    def test_class_variable(self, bind_source):
        index = bind_source("class Foo:\n    x = 1\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test:Foo:x" in symbols
        assert symbols["test:Foo:x"].kind == SymbolKind.VARIABLE

    def test_docstring(self, bind_source):
        index = bind_source('class Foo:\n    """A class."""\n    pass\n')
        symbols = {s.symbol_id: s for s in index.symbols}
        assert symbols["test:Foo"].docstring == "A class."

    def test_nested_class_scope(self, bind_fixture):
        index = bind_fixture("class_with_methods.py")
        scopes = {s.scope_id: s for s in index.scopes}
        assert "class_with_methods:Animal" in scopes
        assert "class_with_methods:Animal.__init__" in scopes
        assert "class_with_methods:Animal.speak" in scopes
        assert "class_with_methods:Dog" in scopes


class TestAssignment:
    def test_simple_variable(self, bind_source):
        index = bind_source("x = 1\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test:x" in symbols
        assert symbols["test:x"].kind == SymbolKind.VARIABLE

    def test_typed_variable(self, bind_source):
        index = bind_source("x: int = 1\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test:x" in symbols
        assert symbols["test:x"].type_annotation is not None
        assert symbols["test:x"].type_annotation.raw == "int"

    def test_tuple_unpacking(self, bind_source):
        index = bind_source("a, b = 1, 2\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test:a" in symbols
        assert "test:b" in symbols


class TestShadowing:
    def test_shadowing_suffix(self, bind_source):
        index = bind_source("x = 1\nx = 2\nx = 3\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test:x" in symbols
        assert "test:x$2" in symbols
        assert "test:x$3" in symbols

    def test_shadowing_in_function(self, bind_fixture):
        index = bind_fixture("shadowing.py")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "shadowing:x" in symbols
        assert "shadowing:x$2" in symbols
        assert "shadowing:x$3" in symbols
        assert "shadowing:process:data" in symbols
        assert "shadowing:process:data$2" in symbols
        assert "shadowing:process:data$3" in symbols


class TestImports:
    def test_simple_import(self, bind_source):
        index = bind_source("import os\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test:os" in symbols
        assert symbols["test:os"].kind == SymbolKind.IMPORT

    def test_import_alias(self, bind_source):
        index = bind_source("import sys as system\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test:system" in symbols

    def test_from_import(self, bind_source):
        index = bind_source("from pathlib import Path\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test:Path" in symbols

    def test_fixture_imports(self, bind_fixture):
        index = bind_fixture("imports_example.py")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "imports_example:os" in symbols
        assert "imports_example:system" in symbols
        assert "imports_example:Path" in symbols
        assert "imports_example:OD" in symbols


class TestRelativeImports:
    """Relative imports resolve against the dotted module_path, not the
    physical file path — src-layout prefixes must never leak into
    imported_from (TASK-58)."""

    @staticmethod
    def _bind(adapter, file_path, module_path, source):
        from pypeeker.binder.binder import bind

        source_bytes = source.encode("utf-8")
        tree = adapter.parse(source_bytes)
        return bind(adapter, file_path, source_bytes, tree.root_node, module_path=module_path)

    def test_src_layout_relative_import_has_no_src_prefix(self, adapter):
        index = self._bind(
            adapter,
            "src/pkg/models/index.py",
            "pkg.models.index",
            "from .references import Reference\n",
        )
        symbols = {s.symbol_id: s for s in index.symbols}
        imp = symbols["pkg.models.index:Reference"]
        assert imp.imported_from == "pkg.models.references.Reference"

    def test_init_barrel_relative_import_resolves_within_package(self, adapter):
        # For pkg/models/__init__.py the module_path IS the package, so a
        # level-1 import resolves inside it, not its parent.
        index = self._bind(
            adapter,
            "src/pkg/models/__init__.py",
            "pkg.models",
            "from .user import User\n",
        )
        symbols = {s.symbol_id: s for s in index.symbols}
        assert symbols["pkg.models:User"].imported_from == "pkg.models.user.User"

    def test_multilevel_relative_import(self, adapter):
        index = self._bind(
            adapter,
            "src/pkg/sub/mod.py",
            "pkg.sub.mod",
            "from ..other import thing\n",
        )
        symbols = {s.symbol_id: s for s in index.symbols}
        assert symbols["pkg.sub.mod:thing"].imported_from == "pkg.other.thing"

    def test_multilevel_relative_import_from_init(self, adapter):
        index = self._bind(
            adapter,
            "src/pkg/sub/__init__.py",
            "pkg.sub",
            "from ..other import thing\n",
        )
        symbols = {s.symbol_id: s for s in index.symbols}
        assert symbols["pkg.sub:thing"].imported_from == "pkg.other.thing"

    def test_from_dot_import_sibling_module(self, adapter):
        index = self._bind(
            adapter,
            "src/pkg/models/index.py",
            "pkg.models.index",
            "from . import references\n",
        )
        symbols = {s.symbol_id: s for s in index.symbols}
        imp = symbols["pkg.models.index:references"]
        assert imp.imported_from == "pkg.models.references"

    def test_flat_layout_unchanged(self, adapter):
        # No src root: module_path mirrors the file path; behavior matches
        # the old file-path-based resolution.
        index = self._bind(
            adapter,
            "models/__init__.py",
            "models",
            "from .user import User\n",
        )
        symbols = {s.symbol_id: s for s in index.symbols}
        assert symbols["models:User"].imported_from == "models.user.User"

    def test_relative_import_beyond_root_degrades_gracefully(self, adapter):
        # More dots than packages: nothing sensible to resolve against;
        # falls back to the bare relative part rather than crashing.
        index = self._bind(
            adapter,
            "src/pkg/mod.py",
            "pkg.mod",
            "from ...nowhere import thing\n",
        )
        symbols = {s.symbol_id: s for s in index.symbols}
        imp = symbols["pkg.mod:thing"]
        assert not imp.imported_from.startswith("src.")

    def test_indexing_src_tree_yields_no_src_prefixed_imports(
        self, project_dir, store, adapter
    ):
        # End-to-end through the indexer: a src-layout package full of
        # relative imports must produce only src-stripped imported_from.
        from pypeeker.indexer import index_path

        pkg = project_dir / "src" / "pkg"
        (pkg / "models").mkdir(parents=True)
        (pkg / "__init__.py").write_text("from .models import User\n")
        (pkg / "models" / "__init__.py").write_text("from .user import User\n")
        (pkg / "models" / "user.py").write_text("class User:\n    pass\n")
        (pkg / "app.py").write_text("from .models import User\nfrom . import models\n")

        result = index_path(
            project_dir / "src",
            store=store,
            root=project_dir,
            adapter=adapter,
            src_roots=("src",),
        )
        assert not result.errors

        imported_froms = [
            symbol.imported_from
            for rel in store.list_indexed_files()
            for symbol in store.load(rel).symbols
            if symbol.imported_from
        ]
        assert imported_froms
        assert not any(i.startswith("src.") for i in imported_froms)
        assert "pkg.models.user.User" in imported_froms  # models/__init__ barrel
        assert "pkg.models.User" in imported_froms  # pkg/__init__ barrel
        assert "pkg.models" in imported_froms  # from . import models in app.py


class TestNestedScopes:
    def test_nested_function(self, bind_fixture):
        index = bind_fixture("nested_scopes.py")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "nested_scopes:outer" in symbols
        assert "nested_scopes:outer.inner" in symbols

    def test_module_variable(self, bind_fixture):
        index = bind_fixture("nested_scopes.py")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "nested_scopes:x" in symbols

    def test_class_and_method(self, bind_fixture):
        index = bind_fixture("nested_scopes.py")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "nested_scopes:MyClass" in symbols
        assert "nested_scopes:MyClass.method" in symbols
        assert "nested_scopes:MyClass:class_var" in symbols

    def test_reference_to_module_var(self, bind_fixture):
        index = bind_fixture("nested_scopes.py")
        # x is referenced in method body
        x_refs = [r for r in index.references if r.symbol_id == "nested_scopes:x"]
        assert len(x_refs) >= 1


class TestGlobalNonlocal:
    def test_global_declaration(self, bind_source):
        source = "x = 0\ndef inc():\n    global x\n    x = x + 1\n"
        index = bind_source(source)
        symbols = {s.symbol_id: s for s in index.symbols}
        # The assignment to x inside inc() should create a symbol in module scope
        assert "test:x" in symbols


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
        assert "comprehensions:numbers" in symbols
        assert "comprehensions:squares" in symbols


class TestDecorators:
    def test_decorated_function(self, bind_fixture):
        index = bind_fixture("decorators.py")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "decorators:decorated_function" in symbols
        func = symbols["decorators:decorated_function"]
        assert "my_decorator" in func.decorators

    def test_class_decorators(self, bind_fixture):
        index = bind_fixture("decorators.py")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "decorators:MyClass.static_method" in symbols
        static = symbols["decorators:MyClass.static_method"]
        assert "staticmethod" in static.decorators


class TestVisibility:
    def test_public(self, bind_source):
        index = bind_source("def foo(): pass\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert symbols["test:foo"].visibility == Visibility.PUBLIC

    def test_protected(self, bind_source):
        index = bind_source("def _foo(): pass\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert symbols["test:_foo"].visibility == Visibility.PROTECTED

    def test_private(self, bind_source):
        index = bind_source("class C:\n    def __secret(self): pass\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert symbols["test:C.__secret"].visibility == Visibility.PRIVATE

    def test_dunder(self, bind_source):
        index = bind_source("class C:\n    def __init__(self): pass\n")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert symbols["test:C.__init__"].visibility == Visibility.DUNDER


class TestReferences:
    def test_function_call_reference(self, bind_source):
        index = bind_source("def foo(): pass\nfoo()\n")
        call_refs = [
            r for r in index.references if r.kind == ReferenceKind.CALL and r.resolved
        ]
        assert any(r.symbol_id == "test:foo" for r in call_refs)

    def test_augmented_assignment(self, bind_source):
        index = bind_source("x = 0\nx += 1\n")
        write_refs = [
            r for r in index.references if r.kind == ReferenceKind.WRITE
        ]
        assert len(write_refs) >= 1

    def test_unresolved_reference(self, bind_source):
        # ``print`` is now resolved as a builtin; use a genuinely undefined
        # name to exercise the unresolved path.
        index = bind_source("totally_undefined('hello')\n")
        unresolved = [r for r in index.references if not r.resolved]
        assert any(r.symbol_id == "totally_undefined" for r in unresolved)

    def test_builtin_resolved_as_builtin(self, bind_source):
        index = bind_source("print('hello')\nx = len([1, 2, 3])\n")
        builtins_refs = [
            r for r in index.references if r.symbol_id.startswith("<builtins>.")
        ]
        names = {r.symbol_id for r in builtins_refs}
        assert "<builtins>.print" in names
        assert "<builtins>.len" in names
        assert all(r.resolved for r in builtins_refs)


class TestWithStatement:
    def test_with_as_variable(self, bind_source):
        source = 'with open("f.txt") as f:\n    data = f.read()\n'
        index = bind_source(source)
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test:f" in symbols
        assert symbols["test:f"].kind == SymbolKind.VARIABLE

    def test_with_reference_resolves(self, bind_source):
        source = 'with open("f.txt") as f:\n    data = f.read()\n'
        index = bind_source(source)
        f_refs = [r for r in index.references if r.symbol_id == "test:f"]
        assert len(f_refs) >= 1
        assert all(r.resolved for r in f_refs)


class TestExceptClause:
    def test_except_as_variable(self, bind_source):
        source = 'try:\n    pass\nexcept ValueError as e:\n    print(e)\n'
        index = bind_source(source)
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test:e" in symbols
        assert symbols["test:e"].kind == SymbolKind.VARIABLE

    def test_except_reference_resolves(self, bind_source):
        source = 'try:\n    pass\nexcept ValueError as e:\n    print(e)\n'
        index = bind_source(source)
        e_refs = [r for r in index.references if r.symbol_id == "test:e"]
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
        assert "test:a" in symbols
        assert "test:b" in symbols
        assert "test:c" in symbols


class TestWalrusOperator:
    def test_walrus_in_if(self, bind_source):
        source = 'if (n := 10) > 5:\n    print(n)\n'
        index = bind_source(source)
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test:n" in symbols

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
        data_param = symbols.get("type_annotations:process:data")
        assert data_param is not None
        assert data_param.type_annotation is not None
        assert data_param.type_annotation.raw == "list[int]"

    def test_variable_annotation(self, bind_fixture):
        index = bind_fixture("type_annotations.py")
        symbols = {s.symbol_id: s for s in index.symbols}
        count = symbols.get("type_annotations:count")
        assert count is not None
        assert count.type_annotation is not None
        assert count.type_annotation.raw == "int"


class TestErrorResilience:
    def test_empty_source(self, bind_source):
        index = bind_source("")
        # Only the module itself is a symbol; nothing is declared inside it.
        non_module = [s for s in index.symbols if s.kind != SymbolKind.MODULE]
        assert len(non_module) == 0
        assert len(index.scopes) == 1  # module scope always exists

    def test_syntax_error_partial_parse(self, bind_source):
        # tree-sitter does best-effort parsing even with errors
        source = "def foo(\n    x = 1\n"
        index = bind_source(source)
        # Should not crash — tree-sitter produces a tree with ERROR nodes
        assert index is not None

    def test_only_comments(self, bind_source):
        index = bind_source("# just a comment\n# another one\n")
        non_module = [s for s in index.symbols if s.kind != SymbolKind.MODULE]
        assert len(non_module) == 0
        assert len(index.scopes) == 1

    def test_deeply_nested(self, bind_source):
        source = "class A:\n  class B:\n    class C:\n      def d(self):\n        x = 1\n"
        index = bind_source(source)
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test:A.B.C.d:x" in symbols

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
        refs = [r for r in index.references if r.symbol_id == "test:Foo.bar"]
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
        refs = [r for r in index.references if r.symbol_id == "test:Foo:x"]
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
        refs = [r for r in index.references if r.symbol_id == "test:Foo:x"]
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
        refs = [r for r in index.references if r.symbol_id == "test:Foo.bar"]
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
        self_refs = [r for r in index.references if r.symbol_id == "test:Foo.bar:self"]
        assert len(self_refs) >= 1
        assert any(r.kind == ReferenceKind.READ for r in self_refs)


class TestAnnotationReferences:
    def test_parameter_annotation_is_referenced(self, bind_source):
        source = "from m import Widget\n\ndef f(x: Widget):\n    return x\n"
        index = bind_source(source)
        refs = [r for r in index.references if r.symbol_id == "test:Widget"]
        assert any(r.kind == ReferenceKind.TYPE_ANNOTATION for r in refs)

    def test_return_annotation_is_referenced(self, bind_source):
        source = "from m import Widget\n\ndef f() -> Widget:\n    return None\n"
        index = bind_source(source)
        assert any(
            r.symbol_id == "test:Widget" and r.kind == ReferenceKind.TYPE_ANNOTATION
            for r in index.references
        )

    def test_subscripted_annotation_binds_inner_type(self, bind_source):
        source = "from m import Widget\n\ndef f(xs: list[Widget]):\n    return xs\n"
        index = bind_source(source)
        assert any(r.symbol_id == "test:Widget" for r in index.references)

    def test_default_parameter_annotation_is_referenced(self, bind_source):
        source = "from m import Widget\n\ndef f(x: Widget = None):\n    return x\n"
        index = bind_source(source)
        assert any(r.symbol_id == "test:Widget" for r in index.references)


class TestListLiteralAndSubscriptMutation:
    def test_list_literal_tagged(self, bind_source):
        index = bind_source("def f():\n    a = [1, 2]\n    return a\n")
        sym = {s.symbol_id: s for s in index.symbols}["test:f:a"]
        assert sym.type_annotation is not None and sym.type_annotation.raw == "list"

    def test_list_comprehension_tagged(self, bind_source):
        index = bind_source("def f():\n    a = [x for x in range(3)]\n    return a\n")
        sym = {s.symbol_id: s for s in index.symbols}["test:f:a"]
        assert sym.type_annotation is not None and sym.type_annotation.raw == "list"

    def test_subscript_assignment_is_write(self, bind_source):
        index = bind_source("def f():\n    a = [1]\n    a[0] = 9\n")
        writes = [r for r in index.references
                  if r.symbol_id == "test:f:a" and r.kind == ReferenceKind.WRITE]
        assert len(writes) == 1

    def test_augmented_subscript_is_write(self, bind_source):
        index = bind_source("def f():\n    a = [1]\n    a[0] += 9\n")
        writes = [r for r in index.references
                  if r.symbol_id == "test:f:a" and r.kind == ReferenceKind.WRITE]
        assert len(writes) == 1

    def test_nested_subscript_root(self, bind_source):
        index = bind_source("def f():\n    a = [[1]]\n    a[0][0] = 9\n")
        writes = [r for r in index.references
                  if r.symbol_id == "test:f:a" and r.kind == ReferenceKind.WRITE]
        assert len(writes) == 1

    def test_plain_read_not_a_write(self, bind_source):
        index = bind_source("def f():\n    a = [1]\n    b = a[0]\n    return b\n")
        writes = [r for r in index.references
                  if r.symbol_id == "test:f:a" and r.kind == ReferenceKind.WRITE]
        assert writes == []


class TestReturnReadRecording:
    """Regression: bare `return <local>` must record a read (id(node) reuse bug)."""

    def test_bare_return_records_read(self, bind_source):
        index = bind_source("def f():\n    e = 1\n    return e\n")
        reads = [r for r in index.references
                 if r.symbol_id == "test:f:e" and r.kind == ReferenceKind.READ]
        assert len(reads) == 1

    def test_bare_return_param_records_read(self, bind_source):
        index = bind_source("def f(a):\n    return a\n")
        assert any(r.symbol_id == "test:f:a" and r.kind == ReferenceKind.READ
                   for r in index.references)
