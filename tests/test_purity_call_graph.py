"""Tests for cross-file call graph + transitive is_pure."""

from __future__ import annotations

from pypeeker.analysis import (
    BareCall,
    TransitiveImpureCall,
    call_graph,
    functions_reachable_from,
    is_pure,
    is_pure,
    is_pure,
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
        assert "mod:helper" in graph["mod:caller"]

    def test_cross_file_edge(self, indexed_project):
        _, store = indexed_project({
            "lib.py": "def helper():\n    return 1\n",
            "app.py": (
                "from lib import helper\n\n"
                "def caller():\n    return helper()\n"
            ),
        })
        graph = call_graph(store)
        assert "lib:helper" in graph["app:caller"]

    def test_self_recursion_excluded(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "def fib(n):\n    return fib(n - 1) + fib(n - 2)\n",
        })
        graph = call_graph(store)
        assert "mod:fib" not in graph.get("mod:fib", frozenset())

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
        assert functions_reachable_from(graph, "mod:a") == frozenset({
            "mod:a", "mod:b", "mod:c"
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
        # wrapper has no impurity in its own body but calls an impure helper —
        # is_pure follows the call graph and flags it transitively.
        obs = is_pure(store, "mod:wrapper")
        assert obs is not None
        assert any(
            isinstance(o, TransitiveImpureCall) and o.callee == "mod:helper"
            for o in obs
        )

    def test_pure_chain_stays_pure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def add(a, b):\n    return a + b\n\n"
                "def mul(a, b):\n    return add(a, b) + add(a, b)\n"
            )
        })
        result = is_pure(store, "mod:mul")
        assert result is not None and not result

    def test_propagates_through_chain(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def deep():\n    print('hi')\n\n"
                "def mid():\n    deep()\n\n"
                "def top():\n    mid()\n"
            )
        })
        mid = is_pure(store, "mod:mid")
        assert mid is not None
        assert any(
            isinstance(o, TransitiveImpureCall) and o.callee == "mod:deep"
            for o in mid
        )
        top = is_pure(store, "mod:top")
        assert top is not None
        # top's immediate transitive callee is mid (not deep).
        assert any(
            isinstance(o, TransitiveImpureCall) and o.callee == "mod:mid"
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
        obs = is_pure(store, "app:front")
        assert obs is not None
        assert any(
            isinstance(o, TransitiveImpureCall) and o.callee == "lib:writer"
            for o in obs
        )

    def test_directly_impure_function_keeps_local_observations(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "def f():\n    print('hi')\n"
        })
        obs = is_pure(store, "mod:f")
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
        result = is_pure(store, "mod:fib")
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
        result = is_pure(store, "mod:even")
        assert result is not None and not result
