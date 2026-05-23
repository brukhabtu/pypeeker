"""Tests for the topical query modules (writes, calls, graph).

Each query is a single-purpose function returning typed observations.
These tests assert that each query produces the right typed facts in
isolation, independent of any composition.
"""

from __future__ import annotations

from pypeeker.analysis import (
    AnalysisContext,
    AttributeMethodCall,
    AttributeWrite,
    BareCall,
    ContextError,
    ModuleCall,
    Observations,
    OuterScopeWrite,
    ReceiverKind,
    attribute_method_calls,
    attribute_writes,
    bare_calls,
    module_calls,
    outer_scope_writes,
)


def _ctx(indexed_project, src: str, symbol_id: str) -> AnalysisContext:
    _, store = indexed_project({"mod.py": src})
    ctx = AnalysisContext.for_function(store, symbol_id)
    assert not isinstance(ctx, ContextError), ctx
    return ctx


class TestAnalysisContext:
    def test_for_function_resolves_a_module_function(self, indexed_project):
        ctx = _ctx(indexed_project, "def f(a):\n    return a\n", "mod:f")
        assert ctx.function_symbol.name == "f"
        assert ctx.function_scope_id == "mod:f"
        assert ctx.function_scope_id in ctx.subtree

    def test_for_function_returns_context_error_for_unknown_symbol(
        self, indexed_project
    ):
        _, store = indexed_project({"mod.py": "def f(): pass\n"})
        result = AnalysisContext.for_function(store, "mod:nope")
        assert isinstance(result, ContextError)
        assert result.reason == "not_found"

    def test_for_function_returns_context_error_for_class(self, indexed_project):
        _, store = indexed_project({"mod.py": "class C:\n    pass\n"})
        result = AnalysisContext.for_function(store, "mod:C")
        assert isinstance(result, ContextError)
        assert result.reason == "not_a_function"

    def test_local_variable_ids_excludes_parameters(self, indexed_project):
        ctx = _ctx(
            indexed_project,
            "def f(a):\n    x = a + 1\n    return x\n",
            "mod:f",
        )
        assert "mod:f:x" in ctx.local_variable_ids
        assert "mod:f:a" not in ctx.local_variable_ids
        assert "mod:f:a" in ctx.local_symbol_ids


class TestOuterScopeWrites:
    def test_finds_global_write(self, indexed_project):
        ctx = _ctx(
            indexed_project,
            "counter = 0\n\ndef bump():\n    global counter\n    counter += 1\n",
            "mod:bump",
        )
        facts = outer_scope_writes(ctx)
        assert len(facts) == 1
        assert isinstance(facts[0], OuterScopeWrite)
        assert facts[0].target == "mod:counter"

    def test_pure_function_yields_no_writes(self, indexed_project):
        ctx = _ctx(indexed_project, "def f(a, b):\n    return a + b\n", "mod:f")
        assert outer_scope_writes(ctx) == Observations()

    def test_local_assignment_is_not_an_outer_write(self, indexed_project):
        ctx = _ctx(
            indexed_project,
            "def f(a):\n    x = a + 1\n    return x\n",
            "mod:f",
        )
        assert outer_scope_writes(ctx) == Observations()


class TestAttributeWrites:
    def test_finds_self_attribute_write(self, indexed_project):
        ctx = _ctx(
            indexed_project,
            "class Box:\n    def set(self, v):\n        self.value = v\n",
            "mod:Box.set",
        )
        facts = attribute_writes(ctx)
        assert len(facts) == 1
        assert isinstance(facts[0], AttributeWrite)
        assert facts[0].attribute == "value"

    def test_no_attribute_writes_for_pure_function(self, indexed_project):
        ctx = _ctx(indexed_project, "def f(a):\n    return a\n", "mod:f")
        assert attribute_writes(ctx) == Observations()


class TestBareCalls:
    def test_finds_print_call(self, indexed_project):
        ctx = _ctx(indexed_project, "def f():\n    print('hi')\n", "mod:f")
        facts = bare_calls(ctx, frozenset({"print"}))
        assert list(facts) == [BareCall(line=1, name="print")]

    def test_respects_caller_provided_denylist(self, indexed_project):
        ctx = _ctx(indexed_project, "def f():\n    print('hi')\n", "mod:f")
        assert bare_calls(ctx, frozenset()) == Observations()

    def test_does_not_match_resolved_calls(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def helper():\n    return 1\n\n"
                "def f():\n    return helper()\n"
            )
        })
        ctx = AnalysisContext.for_function(store, "mod:f")
        assert not isinstance(ctx, ContextError)
        assert bare_calls(ctx, frozenset({"helper"})) == Observations()


class TestAttributeMethodCalls:
    def test_skips_module_rooted_calls(self, indexed_project):
        ctx = _ctx(
            indexed_project,
            "import os\ndef f():\n    os.system('ls')\n",
            "mod:f",
        )
        facts = attribute_method_calls(ctx, frozenset({"system"}))
        assert facts == Observations()

    def test_local_variable_receiver(self, indexed_project):
        ctx = _ctx(
            indexed_project,
            "def f():\n    x = []\n    x.append(1)\n    return x\n",
            "mod:f",
        )
        facts = attribute_method_calls(ctx, frozenset({"append"}))
        assert len(facts) == 1
        assert isinstance(facts[0], AttributeMethodCall)
        assert facts[0].method == "append"
        assert facts[0].receiver_kind == ReceiverKind.VARIABLE

    def test_parameter_receiver(self, indexed_project):
        ctx = _ctx(
            indexed_project,
            "def f(lst):\n    lst.append(1)\n",
            "mod:f",
        )
        facts = attribute_method_calls(ctx, frozenset({"append"}))
        assert len(facts) == 1
        assert facts[0].receiver_kind == ReceiverKind.PARAMETER

    def test_dynamic_receiver_is_unknown(self, indexed_project):
        ctx = _ctx(
            indexed_project,
            "def f():\n    g().append(1)\n",
            "mod:f",
        )
        facts = attribute_method_calls(ctx, frozenset({"append"}))
        assert len(facts) == 1
        assert facts[0].receiver_kind == ReceiverKind.UNKNOWN

    def test_typed_receiver_carries_type_name(self, indexed_project):
        ctx = _ctx(
            indexed_project,
            "from pathlib import Path\ndef f(p: Path):\n    p.write_text('x')\n",
            "mod:f",
        )
        facts = attribute_method_calls(ctx, frozenset({"write_text"}))
        assert len(facts) == 1
        assert facts[0].receiver_type == "Path"


class TestModuleCalls:
    def test_finds_os_system(self, indexed_project):
        ctx = _ctx(
            indexed_project,
            "import os\ndef f():\n    os.system('ls')\n",
            "mod:f",
        )
        facts = module_calls(ctx, frozenset({"os.system"}))
        assert len(facts) == 1
        assert isinstance(facts[0], ModuleCall)
        assert facts[0].qualified_name == "os.system"

    def test_resolves_submodule_chain(self, indexed_project):
        ctx = _ctx(
            indexed_project,
            "import os\ndef f():\n    os.path.join('a', 'b')\n",
            "mod:f",
        )
        facts = module_calls(ctx, frozenset({"os.path.join"}))
        assert len(facts) == 1
        assert facts[0].qualified_name == "os.path.join"

    def test_uses_imported_from_for_aliased_imports(self, indexed_project):
        ctx = _ctx(
            indexed_project,
            "import os as o\ndef f():\n    o.system('ls')\n",
            "mod:f",
        )
        facts = module_calls(ctx, frozenset({"os.system"}))
        assert len(facts) == 1
        assert facts[0].qualified_name == "os.system"

    def test_respects_denylist(self, indexed_project):
        ctx = _ctx(
            indexed_project,
            "import os\ndef f():\n    os.system('ls')\n",
            "mod:f",
        )
        assert module_calls(ctx, frozenset()) == Observations()
