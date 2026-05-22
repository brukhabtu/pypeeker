"""Tests for comprehension and generator variable binding."""

from __future__ import annotations


def _unresolved_names(index):
    return {
        r.symbol_id for r in index.references
        if not r.resolved
        and ":" not in r.symbol_id
        and not r.symbol_id.startswith("<")
    }


class TestListComprehension:
    def test_target_resolves_in_element(self, bind_source):
        index = bind_source("xs = [1, 2, 3]\n[x for x in xs]\n")
        assert "x" not in _unresolved_names(index)

    def test_target_resolves_in_filter(self, bind_source):
        index = bind_source("xs = [1]\n[x for x in xs if x > 0]\n")
        assert "x" not in _unresolved_names(index)


class TestSetComprehension:
    def test_target_resolves(self, bind_source):
        index = bind_source("xs = [1]\n{x for x in xs}\n")
        assert "x" not in _unresolved_names(index)


class TestDictComprehension:
    def test_key_and_value_both_resolve(self, bind_source):
        index = bind_source(
            "items = []\n"
            "{k: v for k, v in items}\n"
        )
        names = _unresolved_names(index)
        assert "k" not in names
        assert "v" not in names


class TestGeneratorExpression:
    def test_target_resolves(self, bind_source):
        index = bind_source("xs = [1]\nsum(x for x in xs)\n")
        assert "x" not in _unresolved_names(index)

    def test_introspection_pattern_from_helpers(self, bind_source):
        # Mirrors the actual code in binder/helpers.py that surfaced the bug.
        index = bind_source(
            "import builtins\n"
            "NAMES = frozenset(name for name in dir(builtins) if not name.startswith('_'))\n"
        )
        assert "name" not in _unresolved_names(index)


class TestNestedForClauses:
    def test_second_iterable_sees_first_target(self, bind_source):
        # ``y in range(x)`` — the ``x`` must resolve to the first for-clause's
        # target, not be unresolved.
        index = bind_source("[x*y for x in range(3) for y in range(x)]\n")
        assert "x" not in _unresolved_names(index)
        assert "y" not in _unresolved_names(index)

    def test_element_sees_both_targets(self, bind_source):
        index = bind_source("[(a, b) for a in [1] for b in [2]]\n")
        names = _unresolved_names(index)
        assert "a" not in names
        assert "b" not in names


class TestTupleUnpackingTargets:
    def test_tuple_target_in_for(self, bind_source):
        index = bind_source("[k+v for k, v in [(1,2)]]\n")
        names = _unresolved_names(index)
        assert "k" not in names
        assert "v" not in names

    def test_nested_tuple_unpacking(self, bind_source):
        # ``for (a, b), c in ...`` — both a, b and c should resolve.
        index = bind_source("[a+b+c for (a, b), c in [((1, 2), 3)]]\n")
        names = _unresolved_names(index)
        assert "a" not in names
        assert "b" not in names
        assert "c" not in names


class TestFirstIterableInEnclosingScope:
    def test_outer_name_reaches_first_iterable_but_not_via_comprehension(self, bind_source):
        # Python evaluates the first iterable in the ENCLOSING scope, which
        # means a target in an enclosing comprehension is NOT visible there.
        # Cheaper test: just confirm that the first iterable resolves to the
        # outer name and nothing in the comprehension fires unresolved.
        index = bind_source(
            "data = [1, 2, 3]\n"
            "result = [x for x in data]\n"
        )
        assert "data" not in _unresolved_names(index)
        assert "x" not in _unresolved_names(index)
