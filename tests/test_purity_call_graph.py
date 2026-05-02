"""Tests for cross-file call graph + transitive purity (TASK-15)."""

from __future__ import annotations

from pypeeker.analysis import (
    EvidenceKind,
    PurityChecker,
    PurityVerdict,
    check_purity,
    check_purity_transitive,
)
from pypeeker.analysis.call_graph import build_call_graph, reachable_functions


class TestBuildCallGraph:
    def test_intra_file_edge(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def helper():\n    return 1\n\n"
                "def caller():\n    return helper()\n"
            )
        })
        graph = build_call_graph(store)
        assert "mod.py:helper" in graph["mod.py:caller"]

    def test_cross_file_edge(self, indexed_project):
        _, store = indexed_project({
            "lib.py": "def helper():\n    return 1\n",
            "app.py": (
                "from lib import helper\n\n"
                "def caller():\n    return helper()\n"
            ),
        })
        graph = build_call_graph(store)
        assert "lib.py:helper" in graph["app.py:caller"]

    def test_self_recursion_excluded(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "def fib(n):\n    return fib(n - 1) + fib(n - 2)\n",
        })
        graph = build_call_graph(store)
        # Self-edge intentionally skipped — propagation doesn't need it.
        assert "mod.py:fib" not in graph.get("mod.py:fib", frozenset())

    def test_module_level_calls_not_tracked(self, indexed_project):
        # Module-level helper() is a caller from outside any function;
        # not represented as an edge in the function-only call graph.
        _, store = indexed_project({
            "mod.py": "def helper():\n    pass\n\nhelper()\n"
        })
        graph = build_call_graph(store)
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
        graph = build_call_graph(store)
        assert reachable_functions(graph, "mod.py:a") == frozenset({
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
        # Locally, wrapper has no direct impurity (the call to helper is
        # resolved, so it's not picked up by the impure-builtin matcher).
        local = check_purity(store, "mod.py:wrapper")
        assert local.verdict == PurityVerdict.PROBABLY_PURE

        transitive = check_purity_transitive(store, "mod.py:wrapper")
        assert transitive.verdict == PurityVerdict.IMPURE
        assert any(
            e.kind == EvidenceKind.TRANSITIVE_IMPURE_CALL
            and e.target == "mod.py:helper"
            for e in transitive.evidence
        )

    def test_pure_chain_stays_pure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def add(a, b):\n    return a + b\n\n"
                "def mul(a, b):\n    return add(a, b) + add(a, b)\n"
            )
        })
        result = check_purity_transitive(store, "mod.py:mul")
        assert result.verdict == PurityVerdict.PROBABLY_PURE
        assert result.evidence == []

    def test_propagates_through_chain(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def deep():\n    print('hi')\n\n"
                "def mid():\n    deep()\n\n"
                "def top():\n    mid()\n"
            )
        })
        # mid is flagged via direct callee deep; top is flagged via mid.
        mid = check_purity_transitive(store, "mod.py:mid")
        assert mid.verdict == PurityVerdict.IMPURE
        assert any(
            e.target == "mod.py:deep" for e in mid.evidence
        )
        top = check_purity_transitive(store, "mod.py:top")
        assert top.verdict == PurityVerdict.IMPURE
        # top's immediate transitive callee is mid (not deep) — we record
        # the direct edge that introduced impurity, not the full chain.
        assert any(
            e.target == "mod.py:mid" for e in top.evidence
        )

    def test_cross_file_propagation(self, indexed_project):
        _, store = indexed_project({
            "lib.py": (
                "def writer(p):\n"
                "    print(p)\n"
            ),
            "app.py": (
                "from lib import writer\n\n"
                "def front(p):\n"
                "    writer(p)\n"
            ),
        })
        result = check_purity_transitive(store, "app.py:front")
        assert result.verdict == PurityVerdict.IMPURE
        assert any(
            e.kind == EvidenceKind.TRANSITIVE_IMPURE_CALL
            and e.target == "lib.py:writer"
            for e in result.evidence
        )

    def test_directly_impure_function_keeps_local_evidence(self, indexed_project):
        # If the function is impure on its own, transitive doesn't replace
        # the local evidence — it appends if there are also transitive
        # callees, or returns the base result unchanged.
        _, store = indexed_project({
            "mod.py": (
                "def f():\n"
                "    print('hi')\n"
            )
        })
        result = check_purity_transitive(store, "mod.py:f")
        assert result.verdict == PurityVerdict.IMPURE
        # The direct print() evidence is preserved.
        assert any(
            e.kind == EvidenceKind.CALLS_IMPURE_BUILTIN for e in result.evidence
        )

    def test_recursion_terminates(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def fib(n):\n"
                "    if n < 2: return n\n"
                "    return fib(n - 1) + fib(n - 2)\n"
            )
        })
        # Pure recursive function — should terminate cleanly.
        result = check_purity_transitive(store, "mod.py:fib")
        assert result.verdict == PurityVerdict.PROBABLY_PURE

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
        result = check_purity_transitive(store, "mod.py:even")
        assert result.verdict == PurityVerdict.PROBABLY_PURE


class TestPurityCheckerAPI:
    def test_check_with_call_graph_method(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def helper():\n    print('hi')\n\n"
                "def wrapper():\n    helper()\n"
            )
        })
        checker = PurityChecker(store)
        assert checker.check("mod.py:wrapper").verdict == PurityVerdict.PROBABLY_PURE
        assert checker.check_with_call_graph("mod.py:wrapper").verdict == PurityVerdict.IMPURE
