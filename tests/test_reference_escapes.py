"""Tests for ``Reference.escapes`` — the binder's local-vs-escaping read signal.

A read is marked ``escapes=False`` only in positions where the value stays in
its local, read-only lifetime and a tuple would behave identically to a list:
iteration, membership (``in``), subscript-element read, and truthiness tests.
Every other position (return/yield/argument/alias/binary/compare/attribute/
bare) is ``escapes=True``. The ``prefer-tuple`` rule relies on this to decide
whether tuplifying a list is safe, so the classification is exercised here in
detail against the real parser.
"""

from __future__ import annotations

import pytest

from pypeeker.models import ReferenceKind, SymbolKind


def _var_read_escapes(bind_source, body: str, var: str = "a") -> list[bool]:
    """Bind ``def f(...): <body>`` and return ``escapes`` for each READ of ``var``.

    ``body`` is the indented function body (without the ``def`` line). The
    variable is bound to a list literal so it is a real local symbol.
    """
    src = "def f(y, other, t):\n" + body
    index = bind_source(src)
    sid = next(
        s.symbol_id
        for s in index.symbols
        if s.name == var and s.kind is SymbolKind.VARIABLE
    )
    return [
        ref.escapes
        for ref in index.references
        if ref.symbol_id == sid and ref.kind is ReferenceKind.READ
    ]


# ── escaping positions — the value leaves or is type-inspected ───────────────

ESCAPING_BODIES = {
    "return": "    a = [1]\n    return a\n",
    "return_in_tuple": "    a = [1]\n    return a, other\n",
    "yield": "    a = [1]\n    yield a\n",
    "call_argument": "    a = [1]\n    print(a)\n",
    "nested_call_argument": "    a = [1]\n    print(sorted(a))\n",
    "mutating_call_argument": "    a = [1]\n    other.extend(a)\n",
    "alias_assignment": "    a = [1]\n    b = a\n",
    "augmented_assignment_rhs": "    a = [1]\n    t += a\n",
    "binary_concat": "    a = [1]\n    b = a + other\n",
    "equality_compare": "    a = [1]\n    b = a == other\n",
    "attribute_method": "    a = [1]\n    a.copy()\n",
    "attribute_read": "    a = [1]\n    b = a.x\n",
    "returned_inside_list": "    a = [1]\n    return [a]\n",
    "fstring_interpolation": '    a = [1]\n    return f"{a}"\n',
    "bare_expression": "    a = [1]\n    a\n",
}


@pytest.mark.parametrize("label", sorted(ESCAPING_BODIES))
def test_escaping_reads_are_marked(bind_source, label):
    escs = _var_read_escapes(bind_source, ESCAPING_BODIES[label])
    assert escs, f"{label}: expected at least one read"
    assert all(escs), f"{label}: expected every read to escape, got {escs}"


# ── local positions — provably tuple-equivalent, non-retaining ───────────────

LOCAL_BODIES = {
    "for_iterable": "    a = [1]\n    for x in a:\n        pass\n",
    "comprehension_iterable": "    a = [1]\n    b = [x for x in a]\n",
    "membership_in": "    a = [1]\n    if y in a:\n        pass\n",
    "membership_not_in": "    a = [1]\n    if y not in a:\n        pass\n",
    "subscript_element_read": "    a = [1]\n    x = a[0]\n",
    "subscript_in_return": "    a = [1]\n    return a[0]\n",
    "if_condition": "    a = [1]\n    if a:\n        pass\n",
    "while_condition": "    a = [1]\n    while a:\n        break\n",
    "assert_expression": "    a = [1]\n    assert a\n",
    "not_operator": "    a = [1]\n    b = not a\n",
    "parenthesized_if": "    a = [1]\n    if (a):\n        pass\n",
}


@pytest.mark.parametrize("label", sorted(LOCAL_BODIES))
def test_local_reads_are_not_marked(bind_source, label):
    escs = _var_read_escapes(bind_source, LOCAL_BODIES[label])
    assert escs, f"{label}: expected at least one read"
    assert not any(escs), f"{label}: expected every read local, got {escs}"


# ── mixed and nuanced cases ──────────────────────────────────────────────────


def test_one_escaping_read_among_local_ones(bind_source):
    # Iterated locally, then returned: the return read escapes even though the
    # iteration read does not.
    escs = _var_read_escapes(
        bind_source,
        "    a = [1]\n    for x in a:\n        pass\n    return a\n",
    )
    assert sorted(escs) == [False, True]


def test_membership_left_operand_escapes(bind_source):
    # ``a in other`` inspects *other*, not a — a is the left operand and its
    # membership in something else is not a contents test of a, so it escapes.
    escs = _var_read_escapes(bind_source, "    a = [1]\n    if a in other:\n        pass\n")
    assert escs == [True]


def test_equality_to_a_list_would_change_result(bind_source):
    # ``a == other`` is escaping precisely because a tuple compares unequal to a
    # list; the rule must never tuplify a compared list.
    escs = _var_read_escapes(bind_source, "    a = [1]\n    return a == other\n")
    assert escs == [True]


def test_escapes_round_trips_through_serialization(bind_source):
    # The flag must survive the on-disk index (to_dict/from_dict), and a
    # pre-field index (missing key) must deserialize to the safe default True.
    from pypeeker.models import FileIndex
    from pypeeker.models.serialize import from_dict, to_dict

    index = bind_source("def f(y):\n    a = [1]\n    for x in a:\n        pass\n    b = [2]\n    return b\n")
    restored = from_dict(FileIndex, to_dict(index))
    by_id = {}
    for ref in restored.references:
        if ref.kind is ReferenceKind.READ:
            by_id.setdefault(ref.symbol_id, []).append(ref.escapes)
    a_id = next(s.symbol_id for s in index.symbols if s.name == "a")
    b_id = next(s.symbol_id for s in index.symbols if s.name == "b")
    assert by_id[a_id] == [False]  # iterated -> local, preserved
    assert by_id[b_id] == [True]  # returned -> escapes, preserved

    # Missing key falls back to the conservative default.
    payload = to_dict(index.references[0])
    payload.pop("escapes")
    assert from_dict(type(index.references[0]), payload).escapes is True


def test_nonlist_variable_reads_still_classified(bind_source):
    # The flag is computed for any simple-name read, not only list bindings.
    src = "def f(y):\n    n = 5\n    return n\n"
    index = bind_source(src)
    sid = next(
        s.symbol_id for s in index.symbols if s.name == "n" and s.kind is SymbolKind.VARIABLE
    )
    escs = [r.escapes for r in index.references if r.symbol_id == sid and r.kind is ReferenceKind.READ]
    assert escs == [True]  # returned
