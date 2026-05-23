"""Tests for the is_pure composition.

The public API is three functions:
* ``is_pure(store, sid) -> list[Observation] | None``
* ``is_pure(store, sid) -> list[Observation] | None``
* ``is_pure(store, sid) -> bool | None``

``None`` means the symbol couldn't be analyzed; ``[]`` means pure;
non-empty list means impure with those observations as evidence.
"""

from __future__ import annotations

import pytest

from pypeeker.analysis import (
    AttributeMethodCall,
    AttributeWrite,
    BareCall,
    ModuleCall,
    OuterScopeWrite,
    is_pure,
    is_pure,
)


def _assert_pure(observations) -> None:
    """Assert the analysis ran and found no impurity."""
    assert observations is not None, "expected pure, got None (unanalyzable)"
    assert not observations, f"expected no observations; got {list(observations)}"


class TestPureFunctions:
    def test_function_only_reading_params_is_pure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "def add(a, b):\n    return a + b\n"
        })
        _assert_pure(is_pure(store, "mod:add"))
        _r = is_pure(store, "mod:add"); assert _r is not None and not _r  # plain bool predicate

    def test_local_assignment_is_pure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "def f(x):\n    y = x + 1\n    z = y * 2\n    return z\n"
        })
        _assert_pure(is_pure(store, "mod:f"))

    def test_local_list_mutation_is_pure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def make_list():\n"
                "    items = []\n"
                "    items.append(1)\n"
                "    items.append(2)\n"
                "    return items\n"
            )
        })
        _assert_pure(is_pure(store, "mod:make_list"))


@pytest.mark.parametrize(
    "src, fn, expected_name",
    [
        ("def shout(x):\n    print(x)\n", "shout", "print"),
        ("def read_file(p):\n    return open(p)\n", "read_file", "open"),
        ("def ask():\n    return input('?')\n", "ask", "input"),
        ("def run():\n    return eval('1+1')\n", "run", "eval"),
        ("def run():\n    return exec('x = 1')\n", "run", "exec"),
    ],
)
def test_impure_builtin_call_is_flagged(indexed_project, src, fn, expected_name):
    _, store = indexed_project({"mod.py": src})
    obs = is_pure(store, f"mod:{fn}")
    assert obs is not None
    assert len(obs) == 1
    assert isinstance(obs[0], BareCall)
    assert obs[0].name == expected_name
    assert bool(is_pure(store, f"mod:{fn}"))


@pytest.mark.parametrize(
    "import_line, fn_body, expected_qualified",
    [
        ("import os", "    os.system(cmd)", "os.system"),
        ("import time", "    return time.time()", "time.time"),
        ("import random", "    return random.random()", "random.random"),
        ("import os", "    os.unlink(p)", "os.unlink"),
        ("import shutil", "    shutil.rmtree(p)", "shutil.rmtree"),
    ],
)
def test_impure_module_call_is_flagged(
    indexed_project, import_line, fn_body, expected_qualified
):
    src = f"{import_line}\ndef f(p, cmd=None):\n{fn_body}\n"
    _, store = indexed_project({"mod.py": src})
    obs = is_pure(store, "mod:f")
    assert obs is not None
    assert any(
        isinstance(o, ModuleCall) and o.qualified_name == expected_qualified
        for o in obs
    )


class TestWritesToOuterScope:
    def test_global_write_is_impure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "counter = 0\n\n"
                "def bump():\n    global counter\n    counter += 1\n"
            )
        })
        obs = is_pure(store, "mod:bump")
        assert obs is not None
        outer = [o for o in obs if isinstance(o, OuterScopeWrite)]
        assert len(outer) == 1
        assert outer[0].target == "mod:counter"

    def test_nonlocal_write_is_impure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def outer():\n"
                "    x = 0\n"
                "    def inner():\n"
                "        nonlocal x\n"
                "        x += 1\n"
                "    return inner\n"
            )
        })
        obs = is_pure(store, "mod:outer.inner")
        assert obs is not None
        assert any(isinstance(o, OuterScopeWrite) for o in obs)

    def test_closure_read_is_pure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def outer():\n"
                "    x = 0\n"
                "    def inner():\n"
                "        return x + 1\n"
                "    return inner\n"
            )
        })
        _assert_pure(is_pure(store, "mod:outer.inner"))


class TestAttributeWrites:
    def test_self_attr_write_is_impure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "class Box:\n"
                "    def set_value(self, v):\n"
                "        self.value = v\n"
            )
        })
        obs = is_pure(store, "mod:Box.set_value")
        assert obs is not None
        attr = [o for o in obs if isinstance(o, AttributeWrite)]
        assert len(attr) == 1
        assert attr[0].attribute == "value"


class TestParameterMutation:
    def test_arg_append_is_impure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "def push(lst, item):\n    lst.append(item)\n"
        })
        obs = is_pure(store, "mod:push")
        assert obs is not None
        assert any(
            isinstance(o, AttributeMethodCall) and o.method == "append"
            for o in obs
        )


class TestUnknownAndEdgeCases:
    def test_symbol_not_found_returns_none(self, indexed_project):
        _, store = indexed_project({"mod.py": "def f(): pass\n"})
        assert is_pure(store, "mod:does_not_exist") is None

    def test_class_symbol_returns_none(self, indexed_project):
        _, store = indexed_project({"mod.py": "class Foo:\n    pass\n"})
        assert is_pure(store, "mod:Foo") is None

    def test_pure_method_is_pure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "class Calc:\n"
                "    def add(self, a, b):\n"
                "        return a + b\n"
            )
        })
        _assert_pure(is_pure(store, "mod:Calc.add"))


class TestEvidenceMetadata:
    def test_observations_include_line_numbers(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def f():\n"
                "    a = 1\n"
                "    print(a)\n"
            )
        })
        obs = is_pure(store, "mod:f")
        assert obs is not None
        assert len(obs) == 1
        assert isinstance(obs[0], BareCall)
        assert obs[0].name == "print"
        # Pypeeker uses 0-indexed lines; print(a) is the third source line.
        assert obs[0].line == 2

    def test_multiple_effects_produce_multiple_observations(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "import os\n"
                "counter = 0\n"
                "\n"
                "def busy():\n"
                "    global counter\n"
                "    counter += 1\n"
                "    print('a')\n"
                "    os.system('ls')\n"
            )
        })
        obs = is_pure(store, "mod:busy")
        assert obs is not None
        assert len(obs) == 3
        types = {type(o) for o in obs}
        assert types == {OuterScopeWrite, BareCall, ModuleCall}
        for o in obs:
            assert 5 <= o.line <= 7


class TestScopeIsolation:
    def test_sibling_impurity_does_not_leak(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def neighbor():\n"
                "    print('side effect')\n"
                "\n"
                "def target(a, b):\n"
                "    return a + b\n"
            )
        })
        _assert_pure(is_pure(store, "mod:target"))

    def test_outer_function_impurity_does_not_leak_into_inner(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def outer():\n"
                "    print('side effect')\n"
                "    def inner(a, b):\n"
                "        return a + b\n"
                "    return inner\n"
            )
        })
        _assert_pure(is_pure(store, "mod:outer.inner"))

    def test_inner_function_impurity_does_not_leak_into_outer(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def outer():\n"
                "    def inner():\n"
                "        print('side effect')\n"
                "    return inner\n"
            )
        })
        _assert_pure(is_pure(store, "mod:outer"))


class TestRedsByLineScope:
    def test_reads_by_line_only_includes_function_under_analysis(
        self, analysis_context
    ):
        ctx = analysis_context(
            "x = 0\n"
            "y = 0\n"
            "\n"
            "def neighbor():\n"
            "    z = x\n"
            "    return z\n"
            "\n"
            "def target():\n"
            "    a = y\n"
            "    return a\n",
            "mod:target",
        )
        for line in ctx.reads_by_line.keys():
            assert line >= 7


class TestTrickyConstructs:
    def test_empty_function_is_pure(self, indexed_project):
        _, store = indexed_project({"mod.py": "def f():\n    pass\n"})
        _assert_pure(is_pure(store, "mod:f"))

    def test_class_method_pass_body_is_pure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "class C:\n    def m(self):\n        pass\n"
        })
        _assert_pure(is_pure(store, "mod:C.m"))

    def test_function_calling_project_internal_function_is_locally_pure(
        self, indexed_project
    ):
        _, store = indexed_project({
            "mod.py": (
                "def helper():\n    return 1\n\n"
                "def caller():\n    return helper()\n"
            )
        })
        _assert_pure(is_pure(store, "mod:caller"))

    def test_class_init_with_self_attr_is_impure(self, indexed_project):
        # Single self.x = y produces one ATTRIBUTE_WRITE observation.
        # The binder's current limitation (only first sequential self.x = y
        # produces a ref) is documented elsewhere as a follow-up.
        _, store = indexed_project({
            "mod.py": (
                "class C:\n"
                "    def __init__(self, a):\n"
                "        self.a = a\n"
            )
        })
        obs = is_pure(store, "mod:C.__init__")
        assert obs is not None
        attr = [o for o in obs if isinstance(o, AttributeWrite)]
        assert len(attr) == 1
        assert attr[0].attribute == "a"

    def test_decorated_function_resolves_normally(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def deco(f):\n    return f\n\n"
                "@deco\n"
                "def f(a, b):\n    return a + b\n"
            )
        })
        _assert_pure(is_pure(store, "mod:f"))

    def test_generator_function_baseline(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "def gen():\n    yield 1\n    yield 2\n"
        })
        _assert_pure(is_pure(store, "mod:gen"))

    def test_lambda_body_does_not_leak_into_outer(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def outer():\n"
                "    f = lambda x: print(x)\n"
                "    return f\n"
            )
        })
        _assert_pure(is_pure(store, "mod:outer"))

    def test_comprehension_with_print_is_impure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "def f(xs):\n    return [print(x) for x in xs]\n"
        })
        obs = is_pure(store, "mod:f")
        assert obs is not None
        assert any(
            isinstance(o, BareCall) and o.name == "print" for o in obs
        )


class TestDenylistOverMatchRegressions:
    """Regressions for names previously over-matched by IO_METHOD_NAMES."""

    def test_local_str_replace_is_pure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def f():\n"
                "    s = 'hello'\n"
                "    return s.replace('h', 'H')\n"
            )
        })
        _assert_pure(is_pure(store, "mod:f"))

    def test_local_object_bind_is_pure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def f(builder):\n"
                "    obj = builder.make()\n"
                "    obj.bind(some_target)\n"
                "    return obj\n"
            )
        })
        obs = is_pure(store, "mod:f")
        assert obs is not None
        bind_obs = [
            o for o in obs
            if isinstance(o, AttributeMethodCall) and o.method == "bind"
        ]
        assert bind_obs == []

    def test_list_remove_on_local_is_pure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def f():\n"
                "    items = [1, 2, 3]\n"
                "    items.remove(2)\n"
                "    return items\n"
            )
        })
        _assert_pure(is_pure(store, "mod:f"))

    def test_list_remove_on_parameter_is_impure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "def f(items):\n    items.remove(2)\n"
        })
        obs = is_pure(store, "mod:f")
        assert obs is not None
        assert any(
            isinstance(o, AttributeMethodCall) and o.method == "remove"
            for o in obs
        )
