"""Additional binder integration tests for coverage gaps."""

import pytest

from pypeeker.models.references import ReferenceKind
from pypeeker.models.scopes import ScopeKind
from pypeeker.models.symbols import SymbolKind

pytestmark = pytest.mark.integration


class TestDecoratedClass:
    def test_class_with_decorator(self, bind_source):
        source = "def my_decorator(cls):\n    return cls\n\n@my_decorator\nclass Foo:\n    pass\n"
        index = bind_source(source)
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test.py:Foo" in symbols
        assert symbols["test.py:Foo"].kind == SymbolKind.CLASS
        assert "my_decorator" in symbols["test.py:Foo"].decorators

    def test_class_with_multiple_decorators(self, bind_source):
        source = (
            "def dec_a(cls): return cls\n"
            "def dec_b(cls): return cls\n\n"
            "@dec_a\n@dec_b\nclass Bar:\n    x = 1\n"
        )
        index = bind_source(source)
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test.py:Bar" in symbols
        assert "dec_a" in symbols["test.py:Bar"].decorators
        assert "dec_b" in symbols["test.py:Bar"].decorators


class TestForWithElse:
    def test_for_else_clause(self, bind_source):
        source = (
            "items = [1, 2, 3]\n"
            "for x in items:\n"
            "    print(x)\n"
            "else:\n"
            "    print('done')\n"
        )
        index = bind_source(source)
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test.py:items" in symbols
        assert "test.py:x" in symbols

    def test_for_loop_variable_declared(self, bind_source):
        source = "for i in range(10):\n    y = i * 2\n"
        index = bind_source(source)
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test.py:i" in symbols
        assert "test.py:y" in symbols


class TestAugmentedAssignmentUnresolved:
    def test_augmented_assign_unresolved(self, bind_source):
        """x += 1 where x was never declared — should create an unresolved WRITE ref."""
        source = "x += 1\n"
        index = bind_source(source)
        write_refs = [r for r in index.references if r.kind == ReferenceKind.WRITE]
        assert len(write_refs) >= 1
        assert any(not r.resolved for r in write_refs)


class TestImportEdgeCases:
    def test_import_dotted_name_no_alias(self, bind_source):
        """import os.path — dotted_name without alias."""
        source = "import os.path\n"
        index = bind_source(source)
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test.py:os.path" in symbols
        assert symbols["test.py:os.path"].kind == SymbolKind.IMPORT

    def test_from_import_single_identifier(self, bind_source):
        """from os import path — identifier import, not dotted_name."""
        source = "from os import path\n"
        index = bind_source(source)
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test.py:path" in symbols
        assert symbols["test.py:path"].kind == SymbolKind.IMPORT

    def test_from_import_aliased_no_alias(self, bind_source):
        """Edge case: from x import y (identifier form, no alias)."""
        source = "from collections import OrderedDict\n"
        index = bind_source(source)
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test.py:OrderedDict" in symbols

    def test_relative_import_single_dot(self, bind_source):
        """from . import utils  in models/__init__.py"""
        source = "from . import utils\n"
        index = bind_source(source, file_path="models/__init__.py")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "models/__init__.py:utils" in symbols

    def test_relative_import_double_dot(self, bind_source):
        """from ..sibling import X in pkg/sub/mod.py"""
        source = "from ..sibling import X\n"
        index = bind_source(source, file_path="pkg/sub/mod.py")
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "pkg/sub/mod.py:X" in symbols
        # Verify the imported_from resolves to pkg.sibling.X
        x_sym = symbols["pkg/sub/mod.py:X"]
        assert x_sym.imported_from == "pkg.sibling.X"

    def test_relative_import_too_deep(self, bind_source):
        """from ...way.too.deep import X in shallow file — should not crash."""
        source = "from ...deep import X\n"
        index = bind_source(source, file_path="top.py")
        # Should handle gracefully, not crash
        assert index is not None
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "top.py:X" in symbols


class TestExtractTargets:
    def test_list_splat_pattern(self, bind_source):
        """a, *rest = [1, 2, 3] — list_splat_pattern."""
        source = "a, *rest = [1, 2, 3]\n"
        index = bind_source(source)
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test.py:a" in symbols
        assert "test.py:rest" in symbols

    def test_tuple_unpacking_in_list(self, bind_source):
        """[a, b] = [1, 2] — list pattern unpacking."""
        source = "[a, b] = [1, 2]\n"
        index = bind_source(source)
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "test.py:a" in symbols
        assert "test.py:b" in symbols


class TestDocstrings:
    def test_single_quoted_docstring(self, bind_source):
        source = "def foo():\n    'A docstring.'\n    pass\n"
        index = bind_source(source)
        symbols = {s.symbol_id: s for s in index.symbols}
        assert symbols["test.py:foo"].docstring == "A docstring."

    def test_function_docstring(self, bind_source):
        source = 'def foo():\n    """Multi-line\n    docstring."""\n    pass\n'
        index = bind_source(source)
        symbols = {s.symbol_id: s for s in index.symbols}
        assert "Multi-line" in symbols["test.py:foo"].docstring


class TestReferenceKinds:
    def test_decorator_reference(self, bind_source):
        source = "def my_dec(f): return f\n\n@my_dec\ndef foo(): pass\n"
        index = bind_source(source)
        dec_refs = [r for r in index.references if r.kind == ReferenceKind.DECORATOR]
        assert any(r.symbol_id == "test.py:my_dec" for r in dec_refs)

    def test_type_annotation_on_variable(self, bind_source):
        source = "class Foo: pass\n\nx: Foo = Foo()\n"
        index = bind_source(source)
        # Type annotation is stored as a symbol attribute
        symbols = {s.symbol_id: s for s in index.symbols}
        assert symbols["test.py:x"].type_annotation is not None
        assert symbols["test.py:x"].type_annotation.raw == "Foo"
