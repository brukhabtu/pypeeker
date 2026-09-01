"""The two shared grammar helpers: ``allow_patterns`` and ``walk``.

``allow_patterns`` is the one spelling of a configured fnmatch allow-list —
eight rule sites used to hand-write the same ``any_of`` — and its contract is
the frozen ``any(fnmatchcase(a, p) or fnmatchcase(b, p) for p in patterns)``:
pattern-major interleaving, and an empty disjunction for no patterns. ``walk``
is the one traversal every whole-tree question is a filter over.
"""

from pypeeker.dsl import (
    UNMATCHED,
    AnyOf,
    Compare,
    EvalContext,
    FieldRead,
    Not,
    all_of,
    allow_patterns,
    any_of,
    not_,
    row,
    walk,
)


def _holds(expr, **fields) -> bool:
    node = expr.evaluate(EvalContext(fields=fields))
    return node.value is not UNMATCHED and bool(node.value)


# ---------------------------------------------------------------------------
# allow_patterns
# ---------------------------------------------------------------------------


def test_allow_patterns_interleaves_columns_per_pattern_in_written_order():
    expr = allow_patterns(("a*", "b*"), row.symbol_id, row.id_module)
    assert isinstance(expr, AnyOf)
    assert [(c.operand.name, c.rhs) for c in expr.children] == [
        ("symbol_id", "a*"),
        ("id_module", "a*"),
        ("symbol_id", "b*"),
        ("id_module", "b*"),
    ]
    assert all(isinstance(c, Compare) and c.op == "matches" for c in expr.children)


def test_allow_patterns_with_no_patterns_is_the_empty_disjunction():
    expr = allow_patterns((), row.symbol_id)
    assert expr.children == ()
    assert not _holds(expr, symbol_id="anything")


def test_allow_patterns_matches_any_column_against_any_pattern():
    expr = allow_patterns(("pkg.mod",), row.symbol_id, row.id_module)
    assert not _holds(expr, symbol_id="pkg.mod:go", id_module="pkg.other")
    assert _holds(expr, symbol_id="pkg.mod:go", id_module="pkg.mod")
    assert _holds(expr, symbol_id="pkg.mod", id_module="x")


def test_allow_patterns_is_fnmatchcase_not_a_prefix_test():
    expr = allow_patterns(("pkg.*:go",), row.symbol_id)
    assert _holds(expr, symbol_id="pkg.mod:go")
    assert not _holds(expr, symbol_id="PKG.mod:go")
    assert not _holds(expr, symbol_id="pkg.mod:go_on")


# ---------------------------------------------------------------------------
# walk
# ---------------------------------------------------------------------------


def test_walk_yields_the_root_and_every_node_beneath_it():
    inner = any_of(row.a.eq(1), not_(row.b.eq(2)))
    expr = all_of(inner, row.c.is_true())
    seen = list(walk(expr))
    assert seen[0] is expr
    assert inner in seen
    assert {node.name for node in seen if isinstance(node, FieldRead)} == {"a", "b", "c"}
    assert any(isinstance(node, Not) for node in seen)


def test_walk_on_a_leaf_yields_only_the_leaf():
    leaf = row.a
    assert list(walk(leaf)) == [leaf]
