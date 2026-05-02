"""Tests for cross-file call graph + transitive purity."""

from __future__ import annotations

from pypeeker.analysis import (
    BareCall,
    TransitiveImpureCall,
    call_graph,
    functions_reachable_from,
    is_pure,
    purity,
    purity_with_call_graph,
)


class TestBuildCallGraph:
    def test_intra_file_edge(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def helper():\n    return 1\n\n"
                "def caller():\n    return helper()\n"
            )
        })
        graph = call_graph(store)
        assert "mod.py:helper" in graph["mod.py:caller"]

    def test_cross_file_edge(self, indexed_project):
        _, store = indexed_project({
            "lib.py": "def helper():\n    return 1\n",
            "app.py": (
                "from lib import helper\n\n"
                "def caller():\n    return helper()\n"
            ),
        })
        graph = call_graph(store)
        assert "lib.py:helper" in graph["app.py:caller"]

    def test_self_recursion_excluded(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "def fib(n):\n    return fib(n - 1) + fib(n - 2)\n",
        })
        graph = call_graph(store)
        assert "mod.py:fib" not in graph.get("mod.py:fib", frozenset())

    def test_module_level_calls_not_tracked(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "def helper():\n    pass\n\nhelper()\n"
        })
        graph = call_graph(store)
        assert graph == {}


class TestReachable:
    def test_bfs_visits_chain(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def c():\n    pass\n\n"
                "def b():\n    return c()\n\n"
                "def a():\n    return b()\n"
            )
        })
        graph = call_graph(store)
        assert functions_reachable_from(graph, "mod.py:a") == frozenset({
            "mod.py:a", "mod.py:b", "mod.py:c"
        })


class TestTransitivePurity:
    def test_wrapper_around_impure_helper_is_flagged(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def helper():\n"
                "    print('hello')\n\n"
                "def wrapper():\n"
                "    helper()\n"
            )
        })
        # Locally pure (the call to helper is resolved, not a builtin match).
        local = purity(store, "mod.py:wrapper")
        assert local is not None and not local

        obs = purity_with_call_graph(store, "mod.py:wrapper")
        assert obs is not None
        assert any(
            isinstance(o, TransitiveImpureCall) and o.callee == "mod.py:helper"
            for o in obs
        )
        # is_pure doesn't follow the call graph; it uses purity() not
        # purity_with_call_graph. Caller selects.
        assert is_pure(store, "mod.py:wrapper") is True

    def test_pure_chain_stays_pure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def add(a, b):\n    return a + b\n\n"
                "def mul(a, b):\n    return add(a, b) + add(a, b)\n"
            )
        })
        result = purity_with_call_graph(store, "mod.py:mul")
        assert result is not None and not result

    def test_propagates_through_chain(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def deep():\n    print('hi')\n\n"
                "def mid():\n    deep()\n\n"
                "def top():\n    mid()\n"
            )
        })
        mid = purity_with_call_graph(store, "mod.py:mid")
        assert mid is not None
        assert any(
            isinstance(o, TransitiveImpureCall) and o.callee == "mod.py:deep"
            for o in mid
        )
        top = purity_with_call_graph(store, "mod.py:top")
        assert top is not None
        # top's immediate transitive callee is mid (not deep).
        assert any(
            isinstance(o, TransitiveImpureCall) and o.callee == "mod.py:mid"
            for o in top
        )

    def test_cross_file_propagation(self, indexed_project):
        _, store = indexed_project({
            "lib.py": "def writer(p):\n    print(p)\n",
            "app.py": (
                "from lib import writer\n\n"
                "def front(p):\n"
                "    writer(p)\n"
            ),
        })
        obs = purity_with_call_graph(store, "app.py:front")
        assert obs is not None
        assert any(
            isinstance(o, TransitiveImpureCall) and o.callee == "lib.py:writer"
            for o in obs
        )

    def test_directly_impure_function_keeps_local_observations(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "def f():\n    print('hi')\n"
        })
        obs = purity_with_call_graph(store, "mod.py:f")
        assert obs is not None
        # Direct print() observation preserved.
        assert any(isinstance(o, BareCall) for o in obs)

    def test_recursion_terminates(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def fib(n):\n"
                "    if n < 2: return n\n"
                "    return fib(n - 1) + fib(n - 2)\n"
            )
        })
        result = purity_with_call_graph(store, "mod.py:fib")
        assert result is not None and not result

    def test_mutual_recursion_terminates(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def even(n):\n"
                "    if n == 0: return True\n"
                "    return odd(n - 1)\n\n"
                "def odd(n):\n"
                "    if n == 0: return False\n"
                "    return even(n - 1)\n"
            )
        })
        result = purity_with_call_graph(store, "mod.py:even")
        assert result is not None and not result
