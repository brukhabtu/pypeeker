"""Tests for keyword-argument handling in the binder.

Keyword names (``func(name=value)``) are syntactic markers, not expressions,
and must not produce identifier references. The value expression is still
a normal subtree and any names inside it must continue to resolve.
"""

from __future__ import annotations


def _names(index, predicate=lambda r: True):
    return {r.symbol_id for r in index.references if predicate(r)}


class TestKwargNameNotReferenced:
    def test_simple_kwarg_name_not_in_references(self, bind_source):
        index = bind_source("foo(frozen=True)\n")
        assert "frozen" not in _names(index)

    def test_dataclass_frozen_does_not_flag_frozen(self, bind_source):
        index = bind_source(
            "from dataclasses import dataclass\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class C:\n"
            "    x: int = 1\n"
        )
        unresolved = {r.symbol_id for r in index.references if not r.resolved}
        assert "frozen" not in unresolved

    def test_multiple_kwargs_all_names_dropped(self, bind_source):
        index = bind_source("foo(name='a', value=1, kind='b')\n")
        for kwarg_name in ("name", "value", "kind"):
            assert kwarg_name not in _names(index), (
                f"{kwarg_name!r} should not be a reference"
            )

    def test_nested_kwargs_both_names_dropped(self, bind_source):
        index = bind_source("foo(outer=bar(inner=1))\n")
        names = _names(index)
        assert "outer" not in names
        assert "inner" not in names


class TestKwargValueStillTracked:
    def test_kwarg_value_identifier_is_a_reference(self, bind_source):
        index = bind_source("x = 1\nfoo(arg=x)\n")
        read_refs = [
            r for r in index.references
            if r.symbol_id.endswith(":x") and r.kind.value == "read"
        ]
        assert len(read_refs) == 1, (
            f"expected exactly one read of x; got {read_refs}"
        )

    def test_kwarg_value_call_is_a_call_reference(self, bind_source):
        index = bind_source("foo(arg=bar())\n")
        names = _names(index)
        assert "bar" in names

    def test_kwarg_value_attribute_chain_is_visited(self, bind_source):
        index = bind_source(
            "import os\n"
            "foo(path=os.path.join('a', 'b'))\n"
        )
        # `os` should be a resolved reference (the imported name).
        os_refs = [r for r in index.references if r.symbol_id.endswith(":os")]
        assert len(os_refs) >= 1


class TestKwargValueDoesNotInfectName:
    def test_value_named_same_as_kwarg_still_only_one_ref(self, bind_source):
        # ``foo(x=x)`` — the LHS ``x`` is syntax, the RHS ``x`` is a real read.
        # We expect exactly one reference to x, and it should be a READ.
        index = bind_source("x = 1\nfoo(x=x)\n")
        x_refs = [r for r in index.references if r.symbol_id.endswith(":x")]
        assert len(x_refs) == 1
        assert x_refs[0].kind.value == "read"
