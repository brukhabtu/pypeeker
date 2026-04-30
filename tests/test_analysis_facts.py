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
    OuterScopeWrite,
    find_attribute_method_calls,
    find_attribute_writes,
    find_impure_builtin_calls,
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
        assert any("x" in sid for sid in ctx.local_variable_ids)
        assert not any("a" in sid.split(":")[-1] for sid in ctx.local_variable_ids)


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
        assert facts[0].target.startswith("<unresolved>.")
        assert facts[0].target.endswith(".value")

    def test_no_attribute_writes_for_pure_function(self, indexed_project):
        ctx = _ctx(indexed_project, "def f(a):\n    return a\n", "mod.py:f")
        assert find_attribute_writes(ctx) == []


class TestImpureBuiltinCalls:
    def test_finds_print_call(self, indexed_project):
        ctx = _ctx(indexed_project, "def f():\n    print('hi')\n", "mod.py:f")
        facts = find_impure_builtin_calls(ctx, frozenset({"print"}))
        assert facts == [ImpureBuiltinCall(name="print", line=facts[0].line)]

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
    def test_finds_dotted_call(self, indexed_project):
        ctx = _ctx(
            indexed_project,
            "import os\ndef f():\n    os.system('ls')\n",
            "mod.py:f",
        )
        facts = find_attribute_method_calls(ctx, frozenset({"system"}))
        assert len(facts) == 1
        assert isinstance(facts[0], AttributeMethodCall)
        assert facts[0].method == "system"

    def test_receiver_is_local_variable_flag_for_local_list(self, indexed_project):
        ctx = _ctx(
            indexed_project,
            "def f():\n    x = []\n    x.append(1)\n    return x\n",
            "mod.py:f",
        )
        facts = find_attribute_method_calls(ctx, frozenset({"append"}))
        assert len(facts) == 1
        assert facts[0].receiver_is_local_variable is True

    def test_receiver_is_local_variable_flag_for_parameter(self, indexed_project):
        ctx = _ctx(
            indexed_project,
            "def f(lst):\n    lst.append(1)\n",
            "mod.py:f",
        )
        facts = find_attribute_method_calls(ctx, frozenset({"append"}))
        assert len(facts) == 1
        # Parameter is not in local_variable_ids (only VARIABLE kind is).
        assert facts[0].receiver_is_local_variable is False
