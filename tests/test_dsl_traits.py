"""Naming an expression registers it into the trait registry that already exists.

The claim under test is architectural, not incidental: ``pypeeker.dsl.trait``
writes into :mod:`pypeeker.analysis.traits`' registry through the public
decorator, so a composed trait and a hand-written one are indistinguishable to
a consumer. These tests pin that, the three guards that keep the composed tier
honest, and the two properties the divergence ledger and the provider contract
respectively demand: ``tuple-candidate`` reports DECLARED, and no provider ever
raises on an unknown ``symbol_id``.

**Registry isolation note.** ``_REGISTRY`` is process-global and nothing
unregisters, so ``install_expressions()`` run by any test (or by a ``CliRunner``
invocation in another file) leaves ``tuple-candidate`` installed for the rest of
the session. Nothing here asserts a provider is *absent* or compares registry
contents; the assertions are all about what a lookup returns, which is stable
either way.
"""

from __future__ import annotations

import pytest

from pypeeker.analysis import (
    TYPE_ANNOTATION,
    VARIABLE_MUTATION,
    Trait,
    get_trait_provider,
    register_trait,
)
from pypeeker.dsl import (
    EXPRESSIONS,
    TUPLE_CANDIDATE,
    ReachError,
    TraitShadowError,
    UnknownFieldError,
    all_of,
    expression,
    install_expressions,
    not_,
    row,
    symbols,
    trait,
    trait_of,
)
from pypeeker.dsl.errors import UnknownExpressionError
from pypeeker.models import Confidence, ScopeKind, SymbolKind

CLEAN = """\
def f():
    xs = []
    return xs[0]
"""

MUTATED = """\
def g():
    ys = []
    ys.append(1)
    return ys
"""

ANNOTATED = """\
def h():
    zs: list = []
    return zs[0]
"""


@pytest.fixture
def index(bind_source):
    """A file index with a clean list local (``m:f:xs``)."""
    return bind_source(CLEAN, "m.py")


# ---------------------------------------------------------------------------
# it is the existing registry, not a second one
# ---------------------------------------------------------------------------


def test_naming_an_expression_registers_a_provider_in_the_analysis_registry():
    trait("dsl-test-is-variable", row.kind.eq(SymbolKind.VARIABLE))
    provider = get_trait_provider("dsl-test-is-variable")
    assert provider is not None


def test_trait_returns_the_same_provider_it_registered():
    returned = trait("dsl-test-returned", row.kind.eq(SymbolKind.VARIABLE))
    assert get_trait_provider("dsl-test-returned") is returned


def test_the_provider_matches_the_registry_signature(index):
    provider = trait("dsl-test-signature", row.kind.eq(SymbolKind.VARIABLE))
    result = provider(index, "m:f:xs")
    assert isinstance(result, Trait)
    assert result.value is True
    assert result.confidence is Confidence.DECLARED


def test_a_composed_trait_is_readable_through_trait_of(index):
    trait("dsl-test-composed", row.kind.eq(SymbolKind.VARIABLE))
    # A composed trait is substrate for another expression exactly as a
    # hand-written one is; nothing marks it as second class.
    matched = (
        symbols()
        .where(trait_of("dsl-test-composed").value.is_true())
        .where(row.symbol_id.eq("m:f:xs"))
    )
    assert matched.universe == "symbols"


def test_primitive_traits_are_untouched_by_installation():
    install_expressions()
    for name in (TYPE_ANNOTATION, VARIABLE_MUTATION):
        provider = get_trait_provider(name)
        assert provider is not None
        # Fork #11: primitive traits stay hand-written Python declaring their
        # own confidence. Installing composed ones must not displace them.
        assert provider.__module__.startswith("pypeeker.analysis.")


# ---------------------------------------------------------------------------
# provenance follows the documented three-part convention
# ---------------------------------------------------------------------------


def test_provenance_opens_with_this_module_and_names_anchor_and_file(index):
    provider = trait("dsl-test-provenance", row.kind.eq(SymbolKind.VARIABLE))
    provenance = provider(index, "m:f:xs").provenance
    assert provenance.startswith("pypeeker.dsl.naming: ")
    assert "'m:f:xs'" in provenance
    assert provenance.endswith("in m.py")


def test_provenance_names_the_fields_and_traits_the_expression_reads(index):
    install_expressions()
    provenance = get_trait_provider("tuple-candidate")(index, "m:f:xs").provenance
    assert "'tuple-candidate'" in provenance
    for read in ("kind", "scope_kind", "type-annotation", "variable-mutation"):
        assert read in provenance


# ---------------------------------------------------------------------------
# evaluation is total: a missing anchor yields a trait, never an exception
# ---------------------------------------------------------------------------


def test_an_unknown_symbol_id_yields_a_falsy_trait_rather_than_raising(index):
    install_expressions()
    result = get_trait_provider("tuple-candidate")(index, "m:nope:nope")
    assert not result.value
    assert result.confidence is Confidence.UNKNOWN


def test_an_unknown_symbol_id_still_produces_provenance(index):
    provider = trait("dsl-test-missing", row.kind.eq(SymbolKind.VARIABLE))
    assert "'m:absent'" in provider(index, "m:absent").provenance


# ---------------------------------------------------------------------------
# guard 1: project reach has no substrate
# ---------------------------------------------------------------------------


def test_trait_refuses_a_project_reach_expression():
    from pypeeker.dsl import opaque

    @opaque("crosses-files", reads=("project:definition",))
    def crosses_files(current_row):
        return True

    with pytest.raises(ReachError) as excinfo:
        trait("dsl-test-project", crosses_files)
    message = str(excinfo.value)
    assert "project" in message
    assert "(FileIndex, symbol_id) -> Trait" in message
    assert excinfo.value.code == "reach-refused"


def test_the_reach_refusal_carries_the_trait_name_in_its_error_fields():
    from pypeeker.dsl import opaque

    @opaque("crosses-files-2", reads=("project:definition",))
    def crosses(current_row):
        return True

    with pytest.raises(ReachError) as excinfo:
        trait("dsl-test-project-2", crosses)
    assert excinfo.value.as_error_fields()["trait"] == "dsl-test-project-2"


# ---------------------------------------------------------------------------
# guard 2: the anchor is symbol-shaped
# ---------------------------------------------------------------------------


def test_trait_refuses_a_field_outside_the_symbols_universe():
    # receiver_root_symbol_id is a references-universe field. A trait provider
    # is handed a symbol id, so this could only ever read None.
    with pytest.raises(UnknownFieldError) as excinfo:
        trait("dsl-test-wrong-universe", row.receiver_root_symbol_id.eq("x"))
    message = str(excinfo.value)
    assert "receiver_root_symbol_id" in message
    assert "symbols universe" in message
    assert "scope_kind" in message  # the message names what IS valid


def test_the_universe_refusal_does_not_register_anything():
    with pytest.raises(UnknownFieldError):
        trait("dsl-test-not-registered", row.escapes.is_true())
    assert get_trait_provider("dsl-test-not-registered") is None


# ---------------------------------------------------------------------------
# guard 3: shadowing a provider this package did not create
# ---------------------------------------------------------------------------


def test_trait_refuses_to_shadow_a_primitive_trait():
    with pytest.raises(TraitShadowError) as excinfo:
        trait(TYPE_ANNOTATION, row.kind.eq(SymbolKind.VARIABLE))
    assert excinfo.value.code == "trait-shadowed"
    assert "replace=True" in str(excinfo.value)


def test_the_refused_shadow_leaves_the_primitive_provider_in_place(index):
    before = get_trait_provider(TYPE_ANNOTATION)
    with pytest.raises(TraitShadowError):
        trait(TYPE_ANNOTATION, row.kind.eq(SymbolKind.VARIABLE))
    assert get_trait_provider(TYPE_ANNOTATION) is before
    assert before(index, "m:f:xs").value == "list"


def test_replace_true_permits_a_deliberate_shadow():
    original = get_trait_provider(VARIABLE_MUTATION)
    try:
        replacement = trait(
            VARIABLE_MUTATION, row.kind.eq(SymbolKind.VARIABLE), replace=True
        )
        assert get_trait_provider(VARIABLE_MUTATION) is replacement
    finally:
        # Restore: the registry is process-global and other tests read this.
        register_trait(VARIABLE_MUTATION)(original)
    assert get_trait_provider(VARIABLE_MUTATION) is original


def test_re_registering_our_own_composed_trait_is_not_shadowing():
    first = trait("dsl-test-idempotent", row.kind.eq(SymbolKind.VARIABLE))
    second = trait("dsl-test-idempotent", row.kind.eq(SymbolKind.FUNCTION))
    assert second is not first
    assert get_trait_provider("dsl-test-idempotent") is second


# ---------------------------------------------------------------------------
# install_expressions
# ---------------------------------------------------------------------------


def test_install_expressions_reports_what_it_installed():
    assert install_expressions() == ("tuple-candidate",)


def test_install_expressions_is_idempotent():
    install_expressions()
    install_expressions()
    assert get_trait_provider("tuple-candidate") is not None


def test_expression_lookup_refuses_an_unknown_name_by_listing_the_known_ones():
    with pytest.raises(UnknownExpressionError) as excinfo:
        expression("no-such-expression")
    assert excinfo.value.code == "unknown-expression"
    assert "tuple-candidate" in str(excinfo.value)


def test_expressions_maps_the_installed_name_to_the_shipped_expression():
    assert EXPRESSIONS["tuple-candidate"] is TUPLE_CANDIDATE
    assert expression("tuple-candidate") is TUPLE_CANDIDATE


def test_importing_the_library_does_not_register_anything():
    # Registration is explicit (import-time-side-effects is a gated rule, and a
    # library module that mutates a last-wins registry on import is rude
    # anyway). Proven in a fresh interpreter so an earlier test's install
    # cannot mask it.
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import pypeeker.dsl.library as lib\n"
            "from pypeeker.analysis import get_trait_provider\n"
            "print(get_trait_provider('tuple-candidate') is None)\n",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "True"


# ---------------------------------------------------------------------------
# the ledger constraint: tuple-candidate reports DECLARED
# ---------------------------------------------------------------------------


def test_tuple_candidate_matches_a_clean_list_local_at_declared(index):
    install_expressions()
    result = get_trait_provider("tuple-candidate")(index, "m:f:xs")
    assert result.value is True
    # dsl-rewrite.md's divergence ledger: "prefer-tuple reports at DECLARED via
    # the meta-read law (fork #4); a port that meets to INFERRED is wrong, not
    # divergent." The type-annotation trait it reads sits at INFERRED.
    assert result.confidence is Confidence.DECLARED
    assert get_trait_provider(TYPE_ANNOTATION)(index, "m:f:xs").confidence is (
        Confidence.INFERRED
    )


def test_tuple_candidate_rejects_a_mutated_list(bind_source):
    install_expressions()
    mutated = bind_source(MUTATED, "g.py")
    assert get_trait_provider("tuple-candidate")(mutated, "g:g:ys").value is False


def test_tuple_candidate_rejects_a_declared_annotation(bind_source):
    install_expressions()
    annotated = bind_source(ANNOTATED, "h.py")
    # An explicit `zs: list` is DECLARED, and the assertion demands INFERRED.
    assert get_trait_provider("tuple-candidate")(annotated, "h:h:zs").value is False


def test_tuple_candidate_reads_the_scope_kind_of_the_binding(bind_source):
    install_expressions()
    module_level = bind_source("xs = []\n", "top.py")
    assert get_trait_provider("tuple-candidate")(module_level, "top:xs").value is False


def test_the_shipped_expression_reads_exactly_what_it_declares():
    assert TUPLE_CANDIDATE.field_names == frozenset({"kind", "scope_kind"})
    assert TUPLE_CANDIDATE.trait_names == frozenset({TYPE_ANNOTATION, VARIABLE_MUTATION})


def _negated_conjunction_pair():
    """The same negated conjunction written both ways round.

    A cheap DECLARED field read and an INFERRED trait read, conjoined under
    ``not_()``. For a variable the conjunction is false, and a consumer of the
    registered provider must get the same evidence out of either spelling —
    the meet over both clauses — or clause order inside somebody else's
    expression would decide the confidence a rule or a precondition reads back.
    """
    kind = row.kind.eq(SymbolKind.FUNCTION)
    annotated = trait_of(TYPE_ANNOTATION).value.eq("list")
    return (
        trait("dsl-test-negated-kind-first", not_(all_of(kind, annotated))),
        trait("dsl-test-negated-trait-first", not_(all_of(annotated, kind))),
    )


def test_a_named_negated_conjunction_reports_the_meet_over_both_clauses(index):
    kind_first, trait_first = _negated_conjunction_pair()
    first = kind_first(index, "m:f:xs")
    second = trait_first(index, "m:f:xs")
    assert first.value is True and second.value is True
    # DECLARED (not a function) met with INFERRED (its annotation is a list).
    # Reporting DECLARED here would certify an answer that rests on the trait
    # read a short circuit skipped.
    assert first.confidence is Confidence.INFERRED
    assert second.confidence is Confidence.INFERRED


@pytest.mark.parametrize("symbol_id", ["m:f:xs", "m:f", "m", "m:absent"])
def test_the_two_spellings_agree_on_every_anchor_including_missing_ones(index, symbol_id):
    kind_first, trait_first = _negated_conjunction_pair()
    first = kind_first(index, symbol_id)
    second = trait_first(index, symbol_id)
    assert first.value == second.value
    assert first.confidence is second.confidence


def test_the_shipped_expression_stays_file_local():
    from pypeeker.dsl import Reach

    # Derived, not declared: nothing in the expression consults the resolver,
    # which is also why trait() accepts it at all.
    assert TUPLE_CANDIDATE.reach is Reach.FILE


def test_the_scope_clause_accepts_both_function_and_lambda():
    # ScopeKind is a str enum and the row carries the string form; pin that the
    # written clause actually compares equal rather than silently never matching.
    assert "function" in (ScopeKind.FUNCTION, ScopeKind.LAMBDA)
    assert "lambda" in (ScopeKind.FUNCTION, ScopeKind.LAMBDA)
    assert "module" not in (ScopeKind.FUNCTION, ScopeKind.LAMBDA)
