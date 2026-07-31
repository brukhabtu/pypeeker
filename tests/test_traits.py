"""Tests for the Trait registry (TASK-127) and the ``variable-mutation`` proof migration.

Covers:
- registry semantics (:func:`register_trait` / :func:`get_trait_provider`):
  registration, lookup, last-import-wins duplicates, miss returns ``None``.
- the ``variable-mutation`` trait's derivation on fixture code: mutated
  (subscript write, augmented assignment, mutator method call), escaping,
  and clean variables, with ``Confidence.DECLARED`` and non-empty provenance.
- the two-quantifier parity proof: ``check.rules.prefer_tuple`` (∀ over
  candidates) and ``refactor.preconditions.NotReassigned`` (pointwise) both
  read the same trait and produce unchanged findings / refusal wording.
"""

from __future__ import annotations

import pytest

from pypeeker.analysis import (
    VARIABLE_MUTATION,
    Trait,
    VariableMutation,
    get_trait_provider,
    register_trait,
)
from pypeeker.check.rules import prefer_tuple
from pypeeker.models import Confidence
from pypeeker.refactor.inline import InlineVariableError, InlineVariablePlanner
from pypeeker.refactor.preconditions import NotReassigned
from pypeeker.storage import TransactionStore

# ---------------------------------------------------------------------------
# Registry semantics
# ---------------------------------------------------------------------------


class TestTraitRegistry:
    def test_register_and_lookup(self):
        def _provider(file_index, symbol_id):
            return Trait(value="x", confidence=Confidence.DECLARED, provenance="test")

        register_trait("test-trait-registry")(_provider)
        assert get_trait_provider("test-trait-registry") is _provider

    def test_lookup_miss_returns_none(self):
        assert get_trait_provider("no-such-trait-ever-registered") is None

    def test_last_registration_wins(self):
        def _first(file_index, symbol_id):
            return Trait(value="first", confidence=Confidence.DECLARED, provenance="")

        def _second(file_index, symbol_id):
            return Trait(value="second", confidence=Confidence.DECLARED, provenance="")

        register_trait("test-trait-last-wins")(_first)
        register_trait("test-trait-last-wins")(_second)
        provider = get_trait_provider("test-trait-last-wins")
        assert provider is _second
        assert provider(None, "irrelevant").value == "second"

    def test_decorator_returns_the_function_unchanged(self):
        def _provider(file_index, symbol_id):
            return Trait(value=1, confidence=Confidence.DECLARED, provenance="")

        decorated = register_trait("test-trait-passthrough")(_provider)
        assert decorated is _provider

    def test_variable_mutation_is_registered_via_the_registry(self):
        # The builtin provider self-registers on import (via the analysis
        # barrel), exactly like a builtin check rule or planner does.
        assert get_trait_provider(VARIABLE_MUTATION) is not None


# ---------------------------------------------------------------------------
# variable-mutation trait derivation
# ---------------------------------------------------------------------------


class TestVariableMutationTrait:
    def _trait(self, bind_source, src, symbol_id):
        file_index = bind_source(src)
        provider = get_trait_provider(VARIABLE_MUTATION)
        return provider(file_index, symbol_id)

    def test_clean_variable(self, bind_source):
        trait = self._trait(
            bind_source, "def f():\n    a = [1, 2]\n    return a[0]\n", "test:f:a"
        )
        assert isinstance(trait.value, VariableMutation)
        assert trait.value.has_write_ref is False
        assert trait.value.mutator_call is False
        assert trait.value.escaping_read is False
        assert trait.value.is_mutated is False

    def test_subscript_write_is_mutated(self, bind_source):
        trait = self._trait(
            bind_source, "def f():\n    a = [1]\n    a[0] = 9\n    return a\n", "test:f:a"
        )
        assert trait.value.has_write_ref is True
        assert trait.value.is_mutated is True

    def test_mutator_call_sets_mutator_call_not_write_ref(self, bind_source):
        trait = self._trait(
            bind_source, "def f():\n    a = [1]\n    a.append(2)\n    return a\n", "test:f:a"
        )
        assert trait.value.mutator_call is True
        assert trait.value.has_write_ref is False
        assert trait.value.is_mutated is True

    def test_escaping_read(self, bind_source):
        trait = self._trait(
            bind_source, "def f():\n    a = [1, 2]\n    return a\n", "test:f:a"
        )
        assert trait.value.escaping_read is True
        assert trait.value.is_mutated is False

    def test_confidence_is_declared(self, bind_source):
        trait = self._trait(
            bind_source, "def f():\n    a = [1, 2]\n    return a[0]\n", "test:f:a"
        )
        assert trait.confidence is Confidence.DECLARED

    def test_provenance_is_populated_and_names_the_symbol(self, bind_source):
        trait = self._trait(
            bind_source, "def f():\n    a = [1, 2]\n    return a[0]\n", "test:f:a"
        )
        assert trait.provenance
        assert "test:f:a" in trait.provenance


# ---------------------------------------------------------------------------
# Two-quantifier parity: prefer_tuple (∀) vs NotReassigned (pointwise)
# ---------------------------------------------------------------------------


class TestPreferTupleParity:
    """``prefer_tuple`` quantified over the same trait ``TestVariableMutationTrait`` exercises.

    Migration parity: the old prefer_tuple code (WRITE/READ/CALL scanning
    inline) is gone, so this pins the exact expected finding set the
    extraction must reproduce byte-for-byte, mirroring
    ``tests/test_check_rules.py::TestPreferTuple``.
    """

    def _flagged_names(self, bind_source, src):
        violations = prefer_tuple(bind_source(src), {})
        return {v.message.split("'")[1] for v in violations}

    def test_mixed_candidates_expected_set(self, bind_source):
        src = (
            "def f(other):\n"
            "    a = [1, 2]\n"          # clean -> flagged
            "    for v in a:\n"
            "        print(v)\n"
            "    b = [4, 5]\n"
            "    return b\n"            # escapes -> not flagged
            "\n"
            "def g(other):\n"
            "    c = [0]\n"
            "    c.append(1)\n"         # mutated (call) -> not flagged
            "    return c\n"
            "\n"
            "def h(other):\n"
            "    d = [0]\n"
            "    d[0] = 1\n"            # mutated (write) -> not flagged
            "    return d\n"
        )
        assert self._flagged_names(bind_source, src) == {"a"}

    def test_rule_uses_the_registered_trait_provider(self, bind_source):
        # Prove prefer_tuple actually goes through the registry (not a
        # private inlined copy of the analysis) by swapping the registered
        # provider for one that reports everything as mutated.
        def _always_mutated(file_index, symbol_id):
            return Trait(
                value=VariableMutation(
                    has_write_ref=True, mutator_call=False, escaping_read=False
                ),
                confidence=Confidence.DECLARED,
                provenance="test override",
            )

        register_trait(VARIABLE_MUTATION)(_always_mutated)
        try:
            msgs = self._flagged_names(
                bind_source, "def f():\n    a = [1, 2]\n    return a[0]\n"
            )
            assert msgs == set()
        finally:
            # Restore the real provider (module import re-registers it).
            import pypeeker.analysis.variable_mutation as vm

            register_trait(VARIABLE_MUTATION)(vm._variable_mutation)


class TestNotReassignedParity:
    """``NotReassigned`` verifying the same trait pointwise, wording unchanged.

    Reuses the exact reassignment scenario from
    ``tests/test_inline_variable.py::test_inline_reassigned_refused`` and
    ``tests/test_preconditions.py::TestNotReassigned``.
    """

    def test_reassigned_refusal_wording_unchanged(self, indexed_project):
        _, store = indexed_project({
            "m.py": "def f(a):\n    x = 1\n    x = 2\n    return x\n"
        })
        ts = TransactionStore(store.project_root)
        with pytest.raises(InlineVariableError) as exc:
            InlineVariablePlanner(store, ts).plan("m:f:x")
        assert str(exc.value) == "Variable is reassigned; cannot inline"

    def test_write_ref_refusal_matches_direct_precondition_call(self, indexed_project):
        _, store = indexed_project({
            "m.py": "def f(a):\n    x = [1]\n    x[0] = 2\n    return x[0]\n"
        })
        index = store.load("m.py")
        symbol = next(s for s in index.symbols if s.name == "x")
        result = NotReassigned(symbol, index).evaluate()
        assert not result.ok
        assert result.reason == "Variable is reassigned; cannot inline"

    def test_mutator_call_alone_does_not_fail_not_reassigned(self, indexed_project):
        # A .append() call sets `mutator_call`, not `has_write_ref` — the
        # judgment call documented on VariableMutation: NotReassigned must
        # not fail on it (unlike prefer_tuple, which does treat it as unsafe).
        _, store = indexed_project({
            "m.py": "def f():\n    x = []\n    x.append(1)\n    return 0\n"
        })
        index = store.load("m.py")
        symbol = next(s for s in index.symbols if s.name == "x")
        assert NotReassigned(symbol, index).evaluate().ok

    def test_precondition_reads_the_same_trait_provider(self, indexed_project):
        _, store = indexed_project({
            "m.py": "def f(a):\n    x = 1\n    return x\n"
        })
        index = store.load("m.py")
        symbol = next(s for s in index.symbols if s.name == "x")
        assert NotReassigned(symbol, index).evaluate().ok

        def _always_write_ref(file_index, symbol_id):
            return Trait(
                value=VariableMutation(
                    has_write_ref=True, mutator_call=False, escaping_read=False
                ),
                confidence=Confidence.DECLARED,
                provenance="test override",
            )

        register_trait(VARIABLE_MUTATION)(_always_write_ref)
        try:
            result = NotReassigned(symbol, index).evaluate()
            assert not result.ok
            assert result.reason == "Variable is reassigned; cannot inline"
        finally:
            import pypeeker.analysis.variable_mutation as vm

            register_trait(VARIABLE_MUTATION)(vm._variable_mutation)
