"""Tests for module-level forward-reference resolution in the binder.

Python is effectively two-pass at module scope: every top-level ``def`` /
``class`` / assignment registers its name before any function body executes.
The binder walks the source once top-to-bottom, so the initial pass leaves
forward references unresolved. An end-of-module fixup retries them against
the now-fully-populated module scope.
"""

from __future__ import annotations


def _resolved_ids(index, name):
    """Return resolved symbol_ids for references named ``name``."""
    return [
        r.symbol_id
        for r in index.references
        if r.resolved and r.symbol_id.endswith(f":{name}")
    ]


def _unresolved_names(index):
    return {
        r.symbol_id for r in index.references
        if not r.resolved and ":" not in r.symbol_id and not r.symbol_id.startswith("<")
    }


class TestForwardFunctionRefs:
    def test_function_calls_helper_defined_below(self, bind_source):
        src = (
            "def caller():\n"
            "    return helper()\n"
            "\n"
            "def helper():\n"
            "    return 1\n"
        )
        index = bind_source(src)
        resolved = _resolved_ids(index, "helper")
        assert resolved, "forward call to helper() should resolve"
        assert "helper" not in _unresolved_names(index)

    def test_two_callers_both_resolve(self, bind_source):
        src = (
            "def a():\n"
            "    return helper()\n"
            "\n"
            "def b():\n"
            "    return helper()\n"
            "\n"
            "def helper():\n"
            "    return 1\n"
        )
        index = bind_source(src)
        assert len(_resolved_ids(index, "helper")) == 2


class TestForwardClassRefs:
    def test_function_references_class_defined_below(self, bind_source):
        src = (
            "def make():\n"
            "    return Widget()\n"
            "\n"
            "class Widget:\n"
            "    pass\n"
        )
        index = bind_source(src)
        assert _resolved_ids(index, "Widget")
        assert "Widget" not in _unresolved_names(index)


class TestForwardModuleConstantRefs:
    def test_function_references_constant_defined_below(self, bind_source):
        src = (
            "def get():\n"
            "    return DEFAULT\n"
            "\n"
            "DEFAULT = 42\n"
        )
        index = bind_source(src)
        assert _resolved_ids(index, "DEFAULT")


class TestNestedScopeStillReaches:
    def test_nested_function_resolves_module_helper(self, bind_source):
        src = (
            "def outer():\n"
            "    def inner():\n"
            "        return helper()\n"
            "    return inner\n"
            "\n"
            "def helper():\n"
            "    return 1\n"
        )
        index = bind_source(src)
        assert _resolved_ids(index, "helper")


class TestShadowingStillWorks:
    def test_local_assignment_does_not_get_rebound_to_module_symbol(self, bind_source):
        # ``x`` is a parameter — the read of ``x`` should bind to the parameter,
        # not to the module-level ``x``. Use-time lookup found the local, so the
        # fixup never sees an unresolved ref for it.
        src = (
            "def f(x):\n"
            "    return x\n"
            "\n"
            "x = 1\n"
        )
        index = bind_source(src)
        x_reads = [
            r for r in index.references
            if r.kind.value == "read" and r.symbol_id.endswith(":x")
        ]
        assert any(":f:x" in r.symbol_id for r in x_reads), (
            "read of x inside f should resolve to the parameter, not module-level x"
        )

    def test_genuinely_undefined_stays_unresolved(self, bind_source):
        src = "def f():\n    return absolutely_undefined\n"
        index = bind_source(src)
        assert "absolutely_undefined" in _unresolved_names(index)


class TestBuiltinNotInterferedWith:
    def test_local_helper_named_like_builtin_still_takes_priority(self, bind_source):
        # User defines their own ``len`` later in the module; an earlier
        # function that calls it should bind to the local, not the builtin.
        src = (
            "def caller():\n"
            "    return len([1, 2])\n"
            "\n"
            "def len(x):\n"
            "    return 0\n"
        )
        index = bind_source(src)
        len_calls = [
            r for r in index.references
            if r.kind.value == "call" and (
                r.symbol_id == "<builtins>.len" or r.symbol_id.endswith(":len")
            )
        ]
        # Exactly one call, and it should point at the local function.
        assert len(len_calls) == 1
        assert ":" in len_calls[0].symbol_id
        assert len_calls[0].symbol_id != "<builtins>.len"
