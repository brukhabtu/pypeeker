"""Tests for the fact-extraction layer.

Facts are atomic observations on the index. These tests assert that each
extractor produces the right typed facts in isolation, independent of any
composite check.
"""

from __future__ import annotations

from pypeeker.analysis import AnalysisContext, ContextError
from pypeeker.analysis.facts import (
    AttributeMethodCall,
    AttributeWrite,
    ImpureBuiltinCall,
    ModuleCall,
    OuterScopeWrite,
    ReceiverKind,
    find_attribute_method_calls,
    find_attribute_writes,
    find_impure_builtin_calls,
    find_module_calls,
    find_outer_scope_writes,
)


def _ctx(indexed_project, src: str, symbol_id: str) -> AnalysisContext:
    _, store = indexed_project({"mod.py": src})
    ctx = AnalysisContext.for_function(store, symbol_id)
    assert not isinstance(ctx, ContextError), ctx
    return ctx


class TestAnalysisContext:
    def test_for_function_resolves_a_module_function(self, indexed_project):
        ctx = _ctx(indexed_project, "def f(a):\n    return a\n", "mod.py:f")
        assert ctx.function_symbol.name == "f"
        assert ctx.function_scope_id == "mod.py:f"
        assert ctx.function_scope_id in ctx.subtree

    def test_for_function_returns_context_error_for_unknown_symbol(
        self, indexed_project
    ):
        _, store = indexed_project({"mod.py": "def f(): pass\n"})
        result = AnalysisContext.for_function(store, "mod.py:nope")
        assert isinstance(result, ContextError)
        assert result.reason == "not_found"

    def test_for_function_returns_context_error_for_class(self, indexed_project):
        _, store = indexed_project({"mod.py": "class C:\n    pass\n"})
        result = AnalysisContext.for_function(store, "mod.py:C")
        assert isinstance(result, ContextError)
        assert result.reason == "not_a_function"

    def test_local_variable_ids_excludes_parameters(self, indexed_project):
        ctx = _ctx(
            indexed_project,
            "def f(a):\n    x = a + 1\n    return x\n",
            "mod.py:f",
        )
        # Exact symbol_id checks: x is a local variable, a is a parameter.
        assert "mod.py:f:x" in ctx.local_variable_ids
        assert "mod.py:f:a" not in ctx.local_variable_ids
        # Verify a is in the broader local_symbol_ids set (just not as VARIABLE).
        assert "mod.py:f:a" in ctx.local_symbol_ids


class TestOuterScopeWrites:
    def test_finds_global_write(self, indexed_project):
        ctx = _ctx(
            indexed_project,
            "counter = 0\n\ndef bump():\n    global counter\n    counter += 1\n",
            "mod.py:bump",
        )
        facts = find_outer_scope_writes(ctx)
        assert len(facts) == 1
        assert isinstance(facts[0], OuterScopeWrite)
        assert facts[0].target_symbol_id == "mod.py:counter"

    def test_pure_function_yields_no_writes(self, indexed_project):
        ctx = _ctx(indexed_project, "def f(a, b):\n    return a + b\n", "mod.py:f")
        assert find_outer_scope_writes(ctx) == []

    def test_local_assignment_is_not_an_outer_write(self, indexed_project):
        ctx = _ctx(
            indexed_project,
            "def f(a):\n    x = a + 1\n    return x\n",
            "mod.py:f",
        )
        assert find_outer_scope_writes(ctx) == []


class TestAttributeWrites:
    def test_finds_self_attribute_write(self, indexed_project):
        ctx = _ctx(
            indexed_project,
            "class Box:\n    def set(self, v):\n        self.value = v\n",
            "mod.py:Box.set",
        )
        facts = find_attribute_writes(ctx)
        assert len(facts) == 1
        assert isinstance(facts[0], AttributeWrite)
        # Exact equality — pypeeker stores attribute writes with the
        # leaf attribute name on the <unresolved> stem.
        assert facts[0].target == "<unresolved>.value"

    def test_no_attribute_writes_for_pure_function(self, indexed_project):
        ctx = _ctx(indexed_project, "def f(a):\n    return a\n", "mod.py:f")
        assert find_attribute_writes(ctx) == []


class TestImpureBuiltinCalls:
    def test_finds_print_call(self, indexed_project):
        # Source has print() on line 1 (0-indexed); assert against a
        # hardcoded expected line, not against facts[0].line itself.
        ctx = _ctx(indexed_project, "def f():\n    print('hi')\n", "mod.py:f")
        facts = find_impure_builtin_calls(ctx, frozenset({"print"}))
        assert facts == [ImpureBuiltinCall(name="print", line=1)]

    def test_respects_caller_provided_denylist(self, indexed_project):
        ctx = _ctx(indexed_project, "def f():\n    print('hi')\n", "mod.py:f")
        # Empty denylist -> no facts even though print() is called.
        assert find_impure_builtin_calls(ctx, frozenset()) == []

    def test_does_not_match_resolved_calls(self, indexed_project):
        # A call to a project-internal function is resolved, not a builtin.
        _, store = indexed_project({
            "mod.py": (
                "def helper():\n    return 1\n\n"
                "def f():\n    return helper()\n"
            )
        })
        ctx = AnalysisContext.for_function(store, "mod.py:f")
        assert not isinstance(ctx, ContextError)
        # Even if 'helper' were on the denylist, it should not match
        # because the call is resolved to a project symbol.
        assert find_impure_builtin_calls(ctx, frozenset({"helper"})) == []


class TestAttributeMethodCalls:
    def test_skips_module_rooted_calls(self, indexed_project):
        # os.system is a module-rooted call — covered by find_module_calls,
        # not by find_attribute_method_calls.
        ctx = _ctx(
            indexed_project,
            "import os\ndef f():\n    os.system('ls')\n",
            "mod.py:f",
        )
        facts = find_attribute_method_calls(ctx, frozenset({"system"}))
        assert facts == []

    def test_local_variable_receiver(self, indexed_project):
        ctx = _ctx(
            indexed_project,
            "def f():\n    x = []\n    x.append(1)\n    return x\n",
            "mod.py:f",
        )
        facts = find_attribute_method_calls(ctx, frozenset({"append"}))
        assert len(facts) == 1
        assert isinstance(facts[0], AttributeMethodCall)
        assert facts[0].method == "append"
        assert facts[0].receiver_kind == ReceiverKind.VARIABLE

    def test_parameter_receiver(self, indexed_project):
        ctx = _ctx(
            indexed_project,
            "def f(lst):\n    lst.append(1)\n",
            "mod.py:f",
        )
        facts = find_attribute_method_calls(ctx, frozenset({"append"}))
        assert len(facts) == 1
        assert facts[0].receiver_kind == ReceiverKind.PARAMETER

    def test_dynamic_receiver_is_unknown(self, indexed_project):
        ctx = _ctx(
            indexed_project,
            "def f():\n    g().append(1)\n",
            "mod.py:f",
        )
        facts = find_attribute_method_calls(ctx, frozenset({"append"}))
        assert len(facts) == 1
        assert facts[0].receiver_kind == ReceiverKind.UNKNOWN


class TestModuleCalls:
    def test_finds_os_system(self, indexed_project):
        ctx = _ctx(
            indexed_project,
            "import os\ndef f():\n    os.system('ls')\n",
            "mod.py:f",
        )
        facts = find_module_calls(ctx, frozenset({"os.system"}))
        assert len(facts) == 1
        assert isinstance(facts[0], ModuleCall)
        assert facts[0].full_name == "os.system"

    def test_resolves_submodule_chain(self, indexed_project):
        ctx = _ctx(
            indexed_project,
            "import os\ndef f():\n    os.path.join('a', 'b')\n",
            "mod.py:f",
        )
        facts = find_module_calls(ctx, frozenset({"os.path.join"}))
        assert len(facts) == 1
        assert facts[0].full_name == "os.path.join"

    def test_uses_imported_from_for_aliased_imports(self, indexed_project):
        # ``import os as o`` — local name is 'o', imported_from is 'os'.
        # Full name should use imported_from.
        ctx = _ctx(
            indexed_project,
            "import os as o\ndef f():\n    o.system('ls')\n",
            "mod.py:f",
        )
        facts = find_module_calls(ctx, frozenset({"os.system"}))
        assert len(facts) == 1
        assert facts[0].full_name == "os.system"

    def test_respects_denylist(self, indexed_project):
        ctx = _ctx(
            indexed_project,
            "import os\ndef f():\n    os.system('ls')\n",
            "mod.py:f",
        )
        # Empty denylist -> no facts.
        assert find_module_calls(ctx, frozenset()) == []
