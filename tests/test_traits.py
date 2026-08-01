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

import subprocess
import sys

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
from pypeeker.models import (
    Confidence,
    FileIndex,
    Location,
    Position,
    Span,
    Symbol,
    SymbolKind,
    TypeAnnotation,
    Visibility,
    to_dict,
)
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
# type-annotation symbol lookup: first-wins + memo coherence (TASK-134)
# ---------------------------------------------------------------------------


def _symbol(symbol_id: str, name: str, raw: str | None) -> Symbol:
    """A minimal VARIABLE symbol carrying ``raw`` as an INFERRED annotation."""
    span = Span(start=Position(line=0, column=0), end=Position(line=0, column=1))
    return Symbol(
        symbol_id=symbol_id,
        name=name,
        kind=SymbolKind.VARIABLE,
        location=Location(file_path="m.py", span=span),
        visibility=Visibility.PUBLIC,
        visibility_confidence=Confidence.DECLARED,
        type_annotation=(
            None if raw is None else TypeAnnotation(raw=raw, confidence=Confidence.INFERRED)
        ),
    )


def _index(symbols, file_hash="h0") -> FileIndex:
    """A bare FileIndex over ``symbols`` — the very list, not a copy.

    The coherence tests below mutate the list they pass in and expect the
    index to see it; copying here would make them pass vacuously.
    """
    return FileIndex(
        file_path="m.py", file_hash=file_hash, language="python", symbols=symbols
    )


class TestTypeAnnotationSymbolLookup:
    """The provider's ``symbol_id -> Symbol`` lookup: value-preserving and coherent.

    TASK-134 replaced a per-call linear scan
    (``next((s for s in file_index.symbols if s.symbol_id == symbol_id), None)``)
    with a map memoized on the ``FileIndex`` instance. Two properties of the
    scan had to survive the swap, and neither is observable from the
    derivation tests above:

    * **first-wins** — the scan returned the *first* matching symbol; a dict
      comprehension would have returned the last. Symbol ids are expected
      unique per file (``$N`` shadowing suffixes), but nothing enforces it,
      and flipping which ``Symbol`` is read flips its ``type_annotation`` —
      i.e. a ``prefer-tuple`` finding or an ``InferredListBinding`` refusal.
    * **coherence** — a scan is always current; a memo is only as current as
      its invalidation guard.
    """

    def _trait(self, file_index, symbol_id):
        provider = get_trait_provider(TYPE_ANNOTATION)
        assert provider is not None
        return provider(file_index, symbol_id)

    def test_duplicate_symbol_id_reports_the_first_binding(self):
        # The property the `setdefault` build preserves: a dict comprehension
        # over the same list would report "dict" here.
        file_index = _index([
            _symbol("m:f:a", "a", "list"),
            _symbol("m:f:a", "a", "dict"),
        ])
        trait = self._trait(file_index, "m:f:a")
        assert trait.value == "list"
        assert is_inferred_list(trait) is True

    def test_duplicate_symbol_id_first_wins_across_repeated_calls(self):
        # The memo must not flip the answer on the second (cached) call.
        file_index = _index([
            _symbol("m:f:a", "a", "list"),
            _symbol("m:f:a", "a", "dict"),
        ])
        assert self._trait(file_index, "m:f:a").value == "list"
        assert self._trait(file_index, "m:f:a").value == "list"

    def test_rebinding_the_symbol_list_is_seen(self):
        file_index = _index([_symbol("m:f:a", "a", "list")])
        assert self._trait(file_index, "m:f:a").value == "list"

        file_index.symbols = [_symbol("m:f:a", "a", "dict")]
        assert self._trait(file_index, "m:f:a").value == "dict"

    def test_appending_a_symbol_is_seen(self):
        file_index = _index([_symbol("m:f:a", "a", "list")])
        assert self._trait(file_index, "m:f:b").value is None

        file_index.symbols.append(_symbol("m:f:b", "b", "set"))
        assert self._trait(file_index, "m:f:b").value == "set"

    def test_replacing_a_symbol_in_place_is_seen_when_the_hash_moves(self):
        # The case list identity and length alone cannot catch: the same list
        # object, the same number of symbols, a different Symbol at index 0.
        # ``file_hash`` is in the guard precisely for this.
        symbols = [_symbol("m:f:a", "a", "list")]
        file_index = _index(symbols, file_hash="h0")
        assert self._trait(file_index, "m:f:a").value == "list"

        symbols[0] = _symbol("m:f:a", "a", "dict")
        file_index.file_hash = "h1"
        assert self._trait(file_index, "m:f:a").value == "dict"

    def test_rewriting_a_symbol_id_in_place_is_seen_when_the_hash_moves(self):
        # The keys, not just the values, can move under a constant length —
        # this is the residual the plan named and ``file_hash`` closes.
        symbols = [_symbol("m:f:a", "a", "list")]
        file_index = _index(symbols, file_hash="h0")
        assert self._trait(file_index, "m:f:a").value == "list"

        symbols[0].symbol_id = "m:f:renamed"
        file_index.file_hash = "h1"
        assert self._trait(file_index, "m:f:a").value is None
        assert self._trait(file_index, "m:f:renamed").value == "list"

    def test_editing_a_symbols_own_fields_needs_no_invalidation(self):
        # Documented consequence of memoizing Symbol *objects* rather than
        # their annotations: an in-place field edit is visible immediately,
        # with no guard involved. Pinned so the guard is never "fixed" to
        # cover a case it does not own.
        symbols = [_symbol("m:f:a", "a", "list")]
        file_index = _index(symbols, file_hash="h0")
        assert self._trait(file_index, "m:f:a").value == "list"

        symbols[0].type_annotation = TypeAnnotation(
            raw="dict", confidence=Confidence.INFERRED
        )
        assert self._trait(file_index, "m:f:a").value == "dict"

    def test_unknown_id_still_yields_the_unknown_trait(self):
        file_index = _index([_symbol("m:f:a", "a", "list")])
        trait = self._trait(file_index, "m:ghost")
        assert trait.value is None
        assert trait.confidence is Confidence.UNKNOWN

    def test_symbol_without_an_annotation_is_unknown(self):
        file_index = _index([_symbol("m:f:a", "a", None)])
        trait = self._trait(file_index, "m:f:a")
        assert trait.value is None
        assert trait.confidence is Confidence.UNKNOWN

    def test_memo_never_reaches_serialization_or_equality(self, bind_source):
        # The memo is a plain instance attribute, not a dataclass field, so
        # ``to_dict`` (which walks ``dataclasses.fields``) and the generated
        # ``__eq__`` must both be blind to it. If it ever became a field it
        # would start showing up in the on-disk index JSON.
        src = "def f():\n    a = [1, 2]\n    return a[0]\n"
        queried = bind_source(src)
        untouched = bind_source(src)

        self._trait(queried, "test:f:a")

        assert to_dict(queried) == to_dict(untouched)
        assert "symbols_by_id" not in repr(to_dict(queried))
        assert queried == untouched


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


BUILTIN_TRAIT_NAMES = (VARIABLE_MUTATION, TYPE_ANNOTATION)
"""Every trait provider pypeeker ships, named explicitly.

Not inferred from a module path. TASK-134 replaced a
``provider.__module__.startswith("pypeeker.analysis.")`` sniff over
``traits._REGISTRY``, which had two problems: it silently under-covered (a
builtin registered from a module the prefix missed, or one wrapped so its
``__module__`` is ``functools``, was skipped with nothing failing), and it
was load-bearing only because the in-process registry is polluted by the
providers ``TestTraitRegistry`` registers and never unregisters.

The conformance loop below iterates this tuple and resolves each provider
through the *public* :func:`~pypeeker.analysis.get_trait_provider`.
``test_manifest_covers_every_builtin_trait`` is what stops it going stale.
"""


class TestProvenanceConvention:
    """Every builtin provider's provenance follows the three-part convention.

    Identification of "builtin" is the explicit ``BUILTIN_TRAIT_NAMES``
    manifest above, resolved through the public registry accessor — no
    ``__module__`` heuristic, and no reach into ``traits._REGISTRY`` from
    this process. (``provider.__module__`` still appears *inside* the
    assertions: part 1 of the provenance convention is that the string opens
    with its producing module, so that is the contract under test, not a way
    of deciding what to test.)
    """

    def test_every_builtin_provider_conforms(self, bind_source):
        src = "def f():\n    a = [1, 2]\n    return a[0]\n"
        file_index = bind_source(src)
        symbol_id = "test:f:a"

        builtins = {}
        for name in BUILTIN_TRAIT_NAMES:
            provider = get_trait_provider(name)
            assert provider is not None, f"{name}: builtin provider is not registered"
            builtins[name] = provider

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

    def test_manifest_covers_every_builtin_trait(self):
        """Discovery, so ``BUILTIN_TRAIT_NAMES`` cannot silently go stale.

        Imports every module under ``pypeeker.analysis`` — including any the
        barrel forgets — in a **fresh interpreter**, then prints the whole
        trait registry. In a clean process the registry contains exactly what
        pypeeker itself registers, so the comparison needs no ``__module__``
        inspection at all: it is not a better heuristic, it is the absence of
        one. Running out of process also means the walk cannot install a
        stray module's registrations into this session for every later test,
        and that the in-process pollution from ``TestTraitRegistry`` (which
        registers providers and never unregisters them) is irrelevant here.

        The subprocess reads ``traits._REGISTRY`` directly: a public
        ``registered_trait_names()`` accessor whose only consumer lives in
        ``tests/`` would trip the gated ``unused-public-symbol`` self-lint
        rule, which indexes ``src`` only. A ``builtin=True`` flag on
        ``register_trait`` is likewise ruled out — ``traits.py`` deliberately
        gives builtin providers the same overridable registry as custom ones.

        A new ``analysis/*.py`` that registers a trait fails this until it is
        added to the manifest, which is what puts it under the provenance
        conformance loop above.
        """
        code = (
            "import importlib, pkgutil\n"
            "import pypeeker.analysis as a\n"
            "for m in pkgutil.walk_packages(a.__path__, a.__name__ + '.'):\n"
            "    importlib.import_module(m.name)\n"
            "from pypeeker.analysis import traits\n"
            "print('\\n'.join(sorted(traits._REGISTRY)))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        discovered = set(result.stdout.split())
        assert discovered == set(BUILTIN_TRAIT_NAMES), (
            "the traits pypeeker registers on import do not match "
            "BUILTIN_TRAIT_NAMES; add the new builtin to the manifest (or "
            "remove the retired one) so the provenance conformance loop "
            f"covers it — discovered {sorted(discovered)}, manifest "
            f"{sorted(BUILTIN_TRAIT_NAMES)}"
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
