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

# TASK-128 adds the ``type-annotation`` trait's derivation tests below (the
# second proven pair): inferred list literal, explicit (DECLARED) annotation,
# a non-list inferred constructor type, no annotation, and an unknown symbol
# id. See TestTypeAnnotationTrait.

from __future__ import annotations

import pytest

from pypeeker.analysis import (
    TYPE_ANNOTATION,
    VARIABLE_MUTATION,
    Trait,
    VariableMutation,
    get_trait_provider,
    is_inferred_list,
    register_trait,
)
from pypeeker.check.rules import prefer_tuple
from pypeeker.models import Confidence
from pypeeker.refactor.inline import InlineVariableError, InlineVariablePlanner
from pypeeker.refactor.preconditions import InferredListBinding
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

    def test_type_annotation_is_registered_via_the_registry(self):
        # The second proven pair's builtin provider self-registers on import
        # too (TASK-128).
        assert get_trait_provider(TYPE_ANNOTATION) is not None


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
# type-annotation trait derivation (TASK-128, second proven pair)
# ---------------------------------------------------------------------------


class TestTypeAnnotationTrait:
    def _trait(self, bind_source, src, symbol_id):
        file_index = bind_source(src)
        provider = get_trait_provider(TYPE_ANNOTATION)
        return provider(file_index, symbol_id)

    def test_inferred_list_literal(self, bind_source):
        trait = self._trait(
            bind_source, "def f():\n    a = [1, 2]\n    return a[0]\n", "test:f:a"
        )
        assert trait.value == "list"
        assert trait.confidence is Confidence.INFERRED
        assert is_inferred_list(trait) is True

    def test_explicit_list_annotation_is_declared_not_inferred(self, bind_source):
        trait = self._trait(
            bind_source, "def f():\n    a: list = [1, 2]\n    return a[0]\n", "test:f:a"
        )
        assert trait.value == "list"
        assert trait.confidence is Confidence.DECLARED
        assert is_inferred_list(trait) is False

    def test_non_list_inferred_constructor_type(self, bind_source):
        trait = self._trait(
            bind_source, "def f():\n    a = Foo()\n    return a\n", "test:f:a"
        )
        assert trait.value == "Foo"
        assert trait.confidence is Confidence.INFERRED
        assert is_inferred_list(trait) is False

    def test_no_annotation_is_unknown_with_none_value(self, bind_source):
        trait = self._trait(bind_source, "def f():\n    a = b + c\n    return a\n", "test:f:a")
        assert trait.value is None
        assert trait.confidence is Confidence.UNKNOWN
        assert is_inferred_list(trait) is False

    def test_unknown_symbol_id_is_unknown_no_exception(self, bind_source):
        trait = self._trait(
            bind_source, "def f():\n    a = [1, 2]\n    return a[0]\n", "test:ghost"
        )
        assert trait.value is None
        assert trait.confidence is Confidence.UNKNOWN

    def test_provenance_names_the_symbol_and_the_module(self, bind_source):
        trait = self._trait(
            bind_source, "def f():\n    a = [1, 2]\n    return a[0]\n", "test:f:a"
        )
        assert trait.provenance
        assert "test:f:a" in trait.provenance
        assert "pypeeker.analysis.type_annotation" in trait.provenance


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


# ---------------------------------------------------------------------------
# Two-quantifier parity, second pair: prefer_tuple (∀) vs InferredListBinding
# (pointwise) over the ``type-annotation`` trait (TASK-128)
# ---------------------------------------------------------------------------


def _restore_type_annotation_provider():
    """Re-register the real ``type-annotation`` provider after an override."""
    import pypeeker.analysis.type_annotation as ta

    register_trait(TYPE_ANNOTATION)(ta._type_annotation)


def _fixed_annotation_trait(value, confidence):
    """A ``type-annotation`` provider that reports ``(value, confidence)`` for everything."""

    def _provider(file_index, symbol_id):
        return Trait(value=value, confidence=confidence, provenance="test override")

    return _provider


class TestPreferTupleAnnotationParity:
    """``prefer_tuple``'s candidate filter, quantified over the ``type-annotation`` trait.

    The ∀ half of the second proven pair. These override the registered
    provider and assert the finding set moves with it — the assertion that
    fails if the rule kept its own inlined copy of
    ``raw == "list" and confidence is INFERRED``.
    """

    def _flagged_names(self, bind_source, src):
        violations = prefer_tuple(bind_source(src), {})
        return {v.message.split("'")[1] for v in violations}

    def test_baseline_flags_the_inferred_list(self, bind_source):
        src = "def f():\n    a = [1, 2]\n    return a[0]\n"
        assert self._flagged_names(bind_source, src) == {"a"}

    def test_override_reporting_no_list_empties_the_finding_set(self, bind_source):
        src = "def f():\n    a = [1, 2]\n    return a[0]\n"
        register_trait(TYPE_ANNOTATION)(
            _fixed_annotation_trait(None, Confidence.UNKNOWN)
        )
        try:
            assert self._flagged_names(bind_source, src) == set()
        finally:
            _restore_type_annotation_provider()
        assert self._flagged_names(bind_source, src) == {"a"}

    def test_override_reporting_inferred_list_makes_a_non_list_a_candidate(
        self, bind_source
    ):
        # The positive direction: a local the real provider says is not a list
        # becomes a candidate purely because the registered trait says so.
        src = "def f():\n    a = 5\n    return a[0]\n"
        assert self._flagged_names(bind_source, src) == set()
        register_trait(TYPE_ANNOTATION)(
            _fixed_annotation_trait("list", Confidence.INFERRED)
        )
        try:
            assert self._flagged_names(bind_source, src) == {"a"}
        finally:
            _restore_type_annotation_provider()

    def test_declared_annotation_override_is_not_a_candidate(self, bind_source):
        # Confidence, not just the raw text, is what the trait carries: a
        # DECLARED ``list`` must stay out of the finding set.
        src = "def f():\n    a = [1, 2]\n    return a[0]\n"
        register_trait(TYPE_ANNOTATION)(
            _fixed_annotation_trait("list", Confidence.DECLARED)
        )
        try:
            assert self._flagged_names(bind_source, src) == set()
        finally:
            _restore_type_annotation_provider()

    def test_finding_confidence_is_not_the_trait_confidence(self, bind_source):
        # The trait's confidence is INFERRED for every candidate; the
        # Violation must keep its default DECLARED, because
        # ``app.check_fixes.auto_fixable`` gates prefer-tuple's autofix on it
        # and ``Violation.__str__`` would otherwise append " [inferred]".
        src = "def f():\n    a = [1, 2]\n    return a[0]\n"
        violations = prefer_tuple(bind_source(src), {})
        assert [v.confidence for v in violations] == [Confidence.DECLARED]


class TestInferredListBindingParity:
    """``InferredListBinding`` verifying the same trait pointwise, wording unchanged."""

    def _precondition(self, indexed_project, src):
        _, store = indexed_project({"m.py": src})
        index = store.load("m.py")
        symbol = next(s for s in index.symbols if s.name == "xs")
        return InferredListBinding("xs", symbol, index)

    def test_inferred_list_passes(self, indexed_project):
        assert self._precondition(
            indexed_project, "def f():\n    xs = [1, 2]\n    return xs[0]\n"
        ).evaluate().ok

    def test_non_list_fails_with_unchanged_wording(self, indexed_project):
        result = self._precondition(
            indexed_project, "def f():\n    xs = 5\n    return xs\n"
        ).evaluate()
        assert not result.ok
        assert result.reason == "'xs' is no longer bound to an inferred list literal"

    def test_precondition_reads_the_same_trait_provider(self, indexed_project):
        precondition = self._precondition(
            indexed_project, "def f():\n    xs = [1, 2]\n    return xs[0]\n"
        )
        assert precondition.evaluate().ok

        register_trait(TYPE_ANNOTATION)(
            _fixed_annotation_trait(None, Confidence.UNKNOWN)
        )
        try:
            result = precondition.evaluate()
            assert not result.ok
            assert result.reason == "'xs' is no longer bound to an inferred list literal"
        finally:
            _restore_type_annotation_provider()

        assert precondition.evaluate().ok

    def test_override_flips_a_non_list_binding_to_passing(self, indexed_project):
        precondition = self._precondition(
            indexed_project, "def f():\n    xs = 5\n    return xs\n"
        )
        assert not precondition.evaluate().ok

        register_trait(TYPE_ANNOTATION)(
            _fixed_annotation_trait("list", Confidence.INFERRED)
        )
        try:
            assert precondition.evaluate().ok
        finally:
            _restore_type_annotation_provider()

    def test_frozen_identifiers_unchanged(self):
        # ``name`` surfaces as SubmitError.precondition / DroppedIntent.
        # precondition; ``slug`` becomes the check --fix refusal reason and
        # TuplifyError.code.
        assert InferredListBinding.name == "inferred-list-binding"
        assert InferredListBinding.slug == "text-mismatch"


# ---------------------------------------------------------------------------
# Provenance-format conformance (TASK-128)
# ---------------------------------------------------------------------------


class TestProvenanceConvention:
    """Every builtin provider's provenance follows the three-part convention.

    Reaches ``traits._REGISTRY`` on purpose: a public
    ``registered_trait_names()`` accessor whose only consumer lives in
    ``tests/`` would trip the gated ``unused-public-symbol`` self-lint rule,
    which indexes ``src`` only.
    """

    def _builtin_providers(self):
        from pypeeker.analysis import traits

        return {
            name: provider
            for name, provider in traits._REGISTRY.items()
            if getattr(provider, "__module__", "").startswith("pypeeker.analysis.")
        }

    def test_every_builtin_provider_conforms(self, bind_source):
        src = "def f():\n    a = [1, 2]\n    return a[0]\n"
        file_index = bind_source(src)
        symbol_id = "test:f:a"

        builtins = self._builtin_providers()
        # Guard against a vacuous pass: both proven pairs must be present.
        assert set(builtins) >= {VARIABLE_MUTATION, TYPE_ANNOTATION}

        for name, provider in builtins.items():
            trait = provider(file_index, symbol_id)
            provenance = trait.provenance
            assert provenance, f"{name}: provenance is empty"
            # Part 1: the producing module's dotted path, then a colon.
            assert provenance.startswith(f"{provider.__module__}: "), (
                f"{name}: provenance does not open with its provider module"
            )
            # Part 3: the anchor this trait was derived for.
            assert symbol_id in provenance, f"{name}: provenance omits the anchor id"
            # Part 2: something was said between the two.
            evidence = provenance[len(provider.__module__) + 2 :]
            assert evidence.split(f"'{symbol_id}'")[0].strip(), (
                f"{name}: provenance names no facts read"
            )

    def test_provenance_is_not_serialized_into_findings(self, bind_source):
        # The guardrail on Trait.provenance: it must never reach CLI JSON, a
        # Violation message, or a refusal reason, or the format freezes into a
        # contract. prefer_tuple is the ∀ consumer that has a trait in hand.
        violations = prefer_tuple(
            bind_source("def f():\n    a = [1, 2]\n    return a[0]\n"), {}
        )
        assert violations
        for violation in violations:
            assert "pypeeker.analysis" not in violation.message
            assert "provenance" not in violation.message
