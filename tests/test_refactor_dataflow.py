"""Tests for refactor/dataflow.py range data-flow analysis."""

from __future__ import annotations

from pypeeker.refactor.dataflow import analyze_range


def test_inputs_and_outputs(indexed_project):
    # range = the single line `c = a + b` (line 2, 0-indexed) in g().
    _, store = indexed_project({
        "m.py": (
            "def g(a, b):\n"        # 0
            "    d = 10\n"          # 1
            "    c = a + b\n"       # 2  <- range
            "    return c + d\n"    # 3
        )
    })
    rdf = analyze_range(store, "m.py", 2, 2)
    assert rdf is not None
    # reads a, b (params, defined outside the range) -> inputs
    assert set(rdf.inputs) == {"m:g:a", "m:g:b"}
    # c is written here and read after -> output; d defined before, not written here
    assert set(rdf.outputs) == {"m:g:c"}


def test_local_defined_and_used_in_range_is_neither(indexed_project):
    _, store = indexed_project({
        "m.py": (
            "def g(a):\n"          # 0
            "    t = a + 1\n"      # 1  <- range start
            "    u = t * 2\n"      # 2  <- range end (t,u local to range)
            "    return a\n"       # 3
        )
    })
    rdf = analyze_range(store, "m.py", 1, 2)
    assert rdf is not None
    assert set(rdf.inputs) == {"m:g:a"}
    # t and u are defined and used within the range, not read after -> no outputs
    assert rdf.outputs == ()


def test_escape_detected(indexed_project):
    _, store = indexed_project({
        "m.py": "def g(a):\n    if a:\n        return 1\n    return 2\n"
    })
    rdf = analyze_range(store, "m.py", 1, 2)  # the if + return
    assert rdf is not None and rdf.has_escape is True


def test_no_escape(indexed_project):
    _, store = indexed_project({
        "m.py": "def g(a):\n    c = a + 1\n    return c\n"
    })
    rdf = analyze_range(store, "m.py", 1, 1)
    assert rdf is not None and rdf.has_escape is False


def test_purity(indexed_project):
    _, store = indexed_project({
        "m.py": (
            "def g(items, x):\n"     # 0
            "    items.append(x)\n"  # 1  <- impure: mutates a parameter
            "    y = x + 1\n"        # 2  <- pure
            "    return y\n"         # 3
        )
    })
    impure = analyze_range(store, "m.py", 1, 1)
    pure = analyze_range(store, "m.py", 2, 2)
    assert impure is not None and impure.is_pure is False
    assert pure is not None and pure.is_pure is True


def test_range_outside_function_returns_none(indexed_project):
    _, store = indexed_project({"m.py": "x = 1\ny = 2\n"})
    assert analyze_range(store, "m.py", 0, 0) is None
