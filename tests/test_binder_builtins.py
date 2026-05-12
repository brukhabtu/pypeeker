"""Tests for builtin resolution in the binder.

The binder introspects the ``builtins`` module at import time and resolves
references to those names so they don't appear as unresolved in the index.
Also exercises ``from __future__ import annotations`` since that's the only
import form whose tree-sitter node type isn't ``import_from_statement``.
"""

from __future__ import annotations

import builtins as _builtins

from pypeeker.binder.helpers import BUILTIN_NAMES, builtin_symbol_id


class TestBuiltinNames:
    def test_includes_common_builtins(self):
        for name in ("len", "list", "dict", "frozenset", "property",
                     "ValueError", "TypeError", "OSError", "True", "False",
                     "None", "NotImplemented"):
            assert name in BUILTIN_NAMES, f"{name} should be in BUILTIN_NAMES"

    def test_excludes_dunder_names(self):
        dunders = [n for n in BUILTIN_NAMES if n.startswith("_")]
        assert dunders == [], f"dunders leaked through: {dunders}"

    def test_introspected_not_hardcoded(self):
        # Every entry must exist on the live ``builtins`` module — proves
        # the set is introspected rather than a stale hardcoded list.
        for name in BUILTIN_NAMES:
            assert hasattr(_builtins, name)


class TestBuiltinFunctionCalls:
    def test_len_call_is_resolved_as_builtin(self, bind_source):
        index = bind_source("def f(x):\n    return len(x)\n")
        len_refs = [r for r in index.references if r.symbol_id == "<builtins>.len"]
        assert len(len_refs) == 1
        assert len_refs[0].resolved is True

    def test_print_call_is_resolved_as_builtin(self, bind_source):
        index = bind_source("def f(x):\n    print(x)\n")
        assert any(
            r.symbol_id == builtin_symbol_id("print") and r.resolved
            for r in index.references
        )


class TestBuiltinTypeReferences:
    def test_list_in_subscript_is_resolved(self, bind_source):
        index = bind_source("x: list[int] = []\n")
        names = {r.symbol_id for r in index.references}
        assert "<builtins>.list" in names
        assert "<builtins>.int" in names

    def test_dict_annotation_is_resolved(self, bind_source):
        index = bind_source("y: dict = {}\n")
        assert any(r.symbol_id == "<builtins>.dict" for r in index.references)


class TestBuiltinExceptions:
    def test_raise_value_error_is_resolved(self, bind_source):
        index = bind_source("def f():\n    raise ValueError('bad')\n")
        assert any(r.symbol_id == "<builtins>.ValueError" for r in index.references)

    def test_except_clause_resolves_builtin(self, bind_source):
        src = (
            "def f():\n"
            "    try:\n"
            "        pass\n"
            "    except KeyError:\n"
            "        pass\n"
        )
        index = bind_source(src)
        assert any(r.symbol_id == "<builtins>.KeyError" for r in index.references)


class TestBuiltinDecorators:
    def test_property_decorator_is_resolved(self, bind_source):
        src = (
            "class C:\n"
            "    @property\n"
            "    def name(self):\n"
            "        return self._name\n"
        )
        index = bind_source(src)
        prop_refs = [r for r in index.references if r.symbol_id == "<builtins>.property"]
        assert len(prop_refs) == 1
        assert prop_refs[0].resolved is True


class TestFutureAnnotations:
    def test_from_future_import_declares_annotations_symbol(self, bind_source):
        index = bind_source("from __future__ import annotations\n")
        symbols = {s.name: s for s in index.symbols}
        assert "annotations" in symbols
        assert symbols["annotations"].imported_from == "__future__.annotations"

    def test_annotations_does_not_appear_as_unresolved(self, bind_source):
        index = bind_source("from __future__ import annotations\n\nx = 1\n")
        unresolved_names = {
            r.symbol_id for r in index.references if not r.resolved
        }
        assert "annotations" not in unresolved_names


class TestProjectSymbolsStillWin:
    def test_local_function_shadows_builtin_name(self, bind_source):
        # If a user defines their own ``len``, references should still bind
        # to that, not the builtin.
        src = (
            "def len(x):\n"
            "    return 0\n"
            "\n"
            "len([1, 2])\n"
        )
        index = bind_source(src)
        call_refs = [
            r for r in index.references
            if r.kind.value == "call"
            and (r.symbol_id == "<builtins>.len" or r.symbol_id.endswith(":len"))
        ]
        assert any(":" in r.symbol_id for r in call_refs), (
            "local len should win over builtin"
        )
        assert not any(r.symbol_id == "<builtins>.len" for r in call_refs)
