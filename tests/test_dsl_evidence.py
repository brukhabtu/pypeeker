"""Evidence-lattice semantics for the DSL: the meet, the meta-read law, prefer-tuple.

This file is the acceptance evidence for fork #4 of ``dsl-rewrite.md``
(confidence composition) and for the divergence ledger's standing constraint
that the ``prefer-tuple`` shape must report ``DECLARED``.
"""

from __future__ import annotations

import itertools

import pytest

from pypeeker.analysis import TYPE_ANNOTATION, VARIABLE_MUTATION, Trait, VariableMutation
from pypeeker.dsl import (
    CONFIDENCE_RANK,
    UNMATCHED,
    Const,
    Derivation,
    EvalContext,
    Expr,
    OpaquePredicateError,
    Reach,
    UnknownTraitError,
    all_of,
    any_of,
    meet,
    not_,
    opaque,
    row,
    trait_of,
)
from pypeeker.models import Confidence

LEVELS = tuple(Confidence)

DECLARED = Confidence.DECLARED
INFERRED = Confidence.INFERRED
HEURISTIC = Confidence.HEURISTIC
UNKNOWN = Confidence.UNKNOWN


def trait(value, confidence, provenance="test: fabricated trait"):
    """Build a Trait without going through a provider."""
    return Trait(value=value, confidence=confidence, provenance=provenance)


def ctx(**traits):
    """An EvalContext carrying only traits."""
    return EvalContext(traits=traits)


def filtering(**traits):
    """A context standing in for a selection's filter stage.

    ``discards_falsity`` is the licence to short-circuit: it asserts that a
    falsy answer drops the row, so a partial meet can never be reported.
    ``Selection.rows`` is the only production caller that sets it.
    """
    return EvalContext(traits=traits, discards_falsity=True)


# --------------------------------------------------------------------------
# The lattice itself
# --------------------------------------------------------------------------


class TestMeetLaws:
    """The meet is a genuine lattice operation over all four Confidence members."""

    def test_ranking_orders_declared_above_inferred_above_heuristic(self):
        assert CONFIDENCE_RANK[DECLARED] > CONFIDENCE_RANK[INFERRED]
        assert CONFIDENCE_RANK[INFERRED] > CONFIDENCE_RANK[HEURISTIC]

    def test_unknown_is_the_bottom_of_the_lattice(self):
        # The fork names three levels; UNKNOWN carries no evidence and sits
        # below all of them. type-annotation returns it for every unannotated
        # symbol, so it genuinely flows through on real data.
        assert CONFIDENCE_RANK[UNKNOWN] < CONFIDENCE_RANK[HEURISTIC]

    def test_empty_meet_is_declared(self):
        assert meet() is DECLARED

    @pytest.mark.parametrize("level", LEVELS)
    def test_identity_is_declared(self, level):
        assert meet(level, DECLARED) is level
        assert meet(DECLARED, level) is level

    @pytest.mark.parametrize("level", LEVELS)
    def test_unknown_absorbs(self, level):
        assert meet(level, UNKNOWN) is UNKNOWN
        assert meet(UNKNOWN, level) is UNKNOWN

    @pytest.mark.parametrize("level", LEVELS)
    def test_idempotent(self, level):
        assert meet(level, level) is level
        assert meet(level) is level

    @pytest.mark.parametrize(("a", "b"), itertools.product(LEVELS, repeat=2))
    def test_commutative(self, a, b):
        assert meet(a, b) is meet(b, a)

    @pytest.mark.parametrize(("a", "b", "c"), itertools.product(LEVELS, repeat=3))
    def test_associative(self, a, b, c):
        assert meet(meet(a, b), c) is meet(a, meet(b, c))

    @pytest.mark.parametrize(("a", "b", "c"), itertools.product(LEVELS, repeat=3))
    def test_order_independent_over_every_permutation(self, a, b, c):
        results = {meet(*perm) for perm in itertools.permutations((a, b, c))}
        assert len(results) == 1

    def test_meet_picks_the_weakest_contribution(self):
        assert meet(DECLARED, INFERRED, DECLARED) is INFERRED
        assert meet(DECLARED, INFERRED, HEURISTIC) is HEURISTIC


# --------------------------------------------------------------------------
# The meta-read law
# --------------------------------------------------------------------------


class TestMetaReadLaw:
    """Reading ``.confidence`` is a DECLARED meta-read (fork #4)."""

    def test_value_read_inherits_the_traits_own_confidence(self):
        node = trait_of("t").value.evaluate(ctx(t=trait("list", INFERRED)))
        assert node.value == "list"
        assert node.confidence is INFERRED

    @pytest.mark.parametrize("level", LEVELS)
    def test_confidence_read_is_always_declared(self, level):
        node = trait_of("t").confidence.evaluate(ctx(t=trait("list", level)))
        # The *value* is the level; the *evidence* for it is DECLARED.
        assert node.value is level
        assert node.confidence is DECLARED

    @pytest.mark.parametrize("level", LEVELS)
    def test_assertion_reports_declared_whether_or_not_it_matches(self, level):
        expr = trait_of("t").at(INFERRED)
        node = expr.evaluate(ctx(t=trait("list", level)))
        assert node.confidence is DECLARED
        if level is INFERRED:
            assert node.value == "list"
        else:
            assert node.value is UNMATCHED

    def test_assertion_at_wrong_level_compares_false_even_on_a_matching_value(self):
        # The sentinel exists so a value that *would* compare equal still
        # loses when the asserted level does not hold.
        expr = trait_of("t").at(DECLARED).eq("list")
        node = expr.evaluate(ctx(t=trait("list", INFERRED)))
        assert node.value is False
        assert node.confidence is DECLARED

    def test_assertion_distinguishes_a_none_valued_trait_from_a_level_miss(self):
        # type-annotation yields None for an unannotated symbol, so "no match"
        # cannot be spelled as None.
        hit = trait_of("t").at(UNKNOWN).evaluate(ctx(t=trait(None, UNKNOWN)))
        miss = trait_of("t").at(DECLARED).evaluate(ctx(t=trait(None, UNKNOWN)))
        assert hit.value is None
        assert miss.value is UNMATCHED
        assert bool(miss.value) is False

    def test_unregistered_trait_is_loud_and_names_what_is_available(self):
        with pytest.raises(UnknownTraitError) as excinfo:
            trait_of("nope").value.evaluate(ctx(t=trait("list", INFERRED)))
        err = excinfo.value
        assert err.code == "unknown-trait"
        assert "nope" in str(err)
        assert "traits available here are: t" in str(err)
        assert err.as_error_fields()["valid"] == ["t"]
        assert err.as_error_fields()["code"] == "unknown-trait"


# --------------------------------------------------------------------------
# The prefer-tuple shape — the ledger's standing constraint
# --------------------------------------------------------------------------


def prefer_tuple_context(*, annotation="list", level=INFERRED, mutation=None):
    """The trait context ``check.rules.prefer_tuple`` reads for one candidate."""
    mutation = mutation or VariableMutation(
        has_write_ref=False, mutator_call=False, escaping_read=False
    )
    return EvalContext(
        fields={"kind": "variable", "scope_kind": "function", "name": "items"},
        traits={
            TYPE_ANNOTATION: trait(annotation, level),
            VARIABLE_MUTATION: trait(mutation, DECLARED),
        },
    )


TUPLE_CANDIDATE = (
    row.kind.eq("variable")
    & row.scope_kind.is_in("function", "lambda")
    & trait_of(TYPE_ANNOTATION).at(INFERRED).eq("list")
    & ~trait_of(VARIABLE_MUTATION).value.attr("is_mutated")
    & ~trait_of(VARIABLE_MUTATION).value.attr("escaping_read")
)
"""The ``prefer-tuple`` shape, written in the DSL."""


class TestPreferTupleReportsDeclared:
    """``dsl-rewrite.md``: a port that meets prefer-tuple to INFERRED is wrong."""

    def test_the_shape_matches_and_reports_declared(self):
        node = TUPLE_CANDIDATE.evaluate(prefer_tuple_context())
        assert node.value is True
        assert node.confidence is DECLARED

    def test_the_uncertified_spelling_keeps_the_traits_own_inferred(self):
        # Control: reading the value without asserting its level is a genuine
        # inference, and must NOT be lifted to DECLARED.
        expr = row.kind.eq("variable") & trait_of(TYPE_ANNOTATION).value.eq("list")
        node = expr.evaluate(prefer_tuple_context())
        assert node.value is True
        assert node.confidence is INFERRED

    def test_the_two_spellings_deliberately_differ(self):
        asserted = trait_of(TYPE_ANNOTATION).at(INFERRED).eq("list")
        unasserted = trait_of(TYPE_ANNOTATION).value.eq("list")
        context = prefer_tuple_context()
        assert asserted.evaluate(context).value is True
        assert unasserted.evaluate(context).value is True
        assert asserted.evaluate(context).confidence is DECLARED
        assert unasserted.evaluate(context).confidence is INFERRED

    def test_a_declared_list_annotation_is_not_a_candidate(self):
        # `x: list = []` is DECLARED, and prefer-tuple deliberately skips it.
        node = TUPLE_CANDIDATE.evaluate(prefer_tuple_context(level=DECLARED))
        assert node.value is False

    def test_a_mutated_binding_is_not_a_candidate(self):
        mutation = VariableMutation(
            has_write_ref=False, mutator_call=True, escaping_read=False
        )
        node = TUPLE_CANDIDATE.evaluate(prefer_tuple_context(mutation=mutation))
        assert node.value is False

    def test_an_unannotated_symbol_flows_unknown_without_raising(self):
        node = TUPLE_CANDIDATE.evaluate(prefer_tuple_context(annotation=None, level=UNKNOWN))
        assert node.value is False

    @pytest.mark.parametrize(
        "order", list(itertools.permutations(range(3)))
    )
    def test_confidence_is_the_same_under_every_clause_order(self, order):
        clauses = [
            trait_of(TYPE_ANNOTATION).at(INFERRED).eq("list"),
            ~trait_of(VARIABLE_MUTATION).value.attr("is_mutated"),
            row.kind.eq("variable"),
        ]
        node = all_of(*(clauses[i] for i in order)).evaluate(prefer_tuple_context())
        assert node.value is True
        assert node.confidence is DECLARED

    @pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
    def test_the_uncertified_shape_is_also_order_independent(self, order):
        clauses = [
            trait_of(TYPE_ANNOTATION).value.eq("list"),
            ~trait_of(VARIABLE_MUTATION).value.attr("is_mutated"),
            row.kind.eq("variable"),
        ]
        node = all_of(*(clauses[i] for i in order)).evaluate(prefer_tuple_context())
        assert node.confidence is INFERRED


# --------------------------------------------------------------------------
# Combinators
# --------------------------------------------------------------------------


class TestCombinators:
    """Written order, short-circuiting, and how each combinator meets."""

    def test_all_of_meets_over_every_operand(self):
        node = all_of(
            Const(True, DECLARED), Const(True, INFERRED), Const(True, HEURISTIC)
        ).evaluate(EvalContext())
        assert node.confidence is HEURISTIC

    def test_all_of_of_nothing_is_true_at_declared(self):
        node = all_of().evaluate(EvalContext())
        assert node.value is True
        assert node.confidence is DECLARED

    def test_all_of_evaluates_in_written_order_and_short_circuits_when_filtering(self):
        calls: list[str] = []

        def recorder(label, result):
            @opaque(label, reads=("name",))
            def _predicate(_row, label=label, result=result):
                calls.append(label)
                return result

            return _predicate

        node = all_of(recorder("first", True), recorder("second", False),
                      recorder("third", True)).evaluate(filtering())
        assert node.value is False
        assert calls == ["first", "second"]
        assert node.detail["short_circuited"] is True

    def test_swapping_clauses_changes_the_schedule_because_there_is_no_optimizer(self):
        calls: list[str] = []

        def recorder(label, result):
            @opaque(label, reads=("name",))
            def _predicate(_row, label=label, result=result):
                calls.append(label)
                return result

            return _predicate

        all_of(recorder("cheap", False), recorder("expensive", True)).evaluate(filtering())
        first_order = list(calls)
        calls.clear()
        all_of(recorder("expensive", True), recorder("cheap", False)).evaluate(filtering())
        assert first_order == ["cheap"]
        assert calls == ["expensive", "cheap"]

    def test_all_of_runs_every_operand_when_its_falsity_is_not_discarded(self):
        # The default context makes no promise that a falsy answer is thrown
        # away, so the conjunction may not skip an operand whose evidence
        # could still be read.
        calls: list[str] = []

        def recorder(label, result):
            @opaque(label, reads=("name",))
            def _predicate(_row, label=label, result=result):
                calls.append(label)
                return result

            return _predicate

        node = all_of(recorder("first", False), recorder("second", True)).evaluate(EvalContext())
        assert node.value is False
        assert calls == ["first", "second"]
        assert node.detail["short_circuited"] is False

    def test_any_of_meets_over_the_satisfied_branches_only(self):
        node = any_of(
            Const(False, UNKNOWN), Const(True, INFERRED), Const(True, DECLARED)
        ).evaluate(EvalContext())
        assert node.value is True
        # The UNKNOWN branch did not hold, so it contributes nothing.
        assert node.confidence is INFERRED

    def test_any_of_meets_over_all_branches_when_none_hold(self):
        node = any_of(Const(False, DECLARED), Const(False, HEURISTIC)).evaluate(EvalContext())
        assert node.value is False
        assert node.confidence is HEURISTIC

    def test_any_of_does_not_short_circuit_so_its_confidence_is_order_free(self):
        calls: list[str] = []

        def recorder(label):
            @opaque(label, reads=("name",))
            def _predicate(_row, label=label):
                calls.append(label)
                return True

            return _predicate

        any_of(recorder("a"), recorder("b")).evaluate(EvalContext())
        assert calls == ["a", "b"]

    @pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
    def test_any_of_confidence_is_identical_under_every_branch_order(self, order):
        branches = [Const(True, INFERRED), Const(True, DECLARED), Const(False, UNKNOWN)]
        node = any_of(*(branches[i] for i in order)).evaluate(EvalContext())
        assert node.confidence is INFERRED

    def test_not_carries_evidence_and_reads_through(self):
        inner = trait_of("t").value.attr("is_mutated")
        node = not_(inner).evaluate(ctx(t=trait(
            VariableMutation(has_write_ref=False, mutator_call=False, escaping_read=False),
            HEURISTIC,
        )))
        assert node.value is True
        assert node.confidence is HEURISTIC
        assert node.reads == frozenset({"trait:t"})

    def test_not_of_an_unmatched_assertion_is_true_at_declared(self):
        node = not_(trait_of("t").at(DECLARED)).evaluate(ctx(t=trait("list", INFERRED)))
        assert node.value is True
        assert node.confidence is DECLARED

    def test_operators_build_the_same_nodes_as_the_named_functions(self):
        a, b = Const(True), Const(False)
        assert (a & b) == all_of(a, b)
        assert (a | b) == any_of(a, b)
        assert (~a) == not_(a)


# --------------------------------------------------------------------------
# Consumed falsity: the short circuit may never lift a certification
# --------------------------------------------------------------------------


class TestConsumedFalsityIsNotLaundered:
    """A conjunction that went false still owes the meet over all its clauses.

    ``not_()`` and ``.eq(False)`` turn a rejection into the answer, so the
    operands a short circuit would have skipped are real contributions. Meeting
    over a prefix instead would make the reported evidence depend on clause
    order, and in the direction that over-claims: ``DECLARED`` for a fact
    resting partly on a read that was never performed. Every test here runs
    both clause orders, because a single order proves nothing about order
    independence.
    """

    @pytest.mark.parametrize("order", list(itertools.permutations(range(2))))
    @pytest.mark.parametrize("context", [EvalContext(), EvalContext(discards_falsity=True)])
    def test_negating_a_false_conjunction_meets_over_every_clause(self, order, context):
        # A DECLARED-false clause and an INFERRED-true one: the honest answer
        # is INFERRED whichever is written first, and the same one De Morgan
        # gives (any_of(not_(a), not_(b))).
        clauses = [Const(False, DECLARED), Const(True, INFERRED)]
        node = not_(all_of(*(clauses[i] for i in order))).evaluate(context)
        assert node.value is True
        assert node.confidence is INFERRED

    @pytest.mark.parametrize("order", list(itertools.permutations(range(2))))
    def test_a_false_conjunction_compared_to_false_meets_over_every_clause(self, order):
        clauses = [Const(False, DECLARED), Const(True, HEURISTIC)]
        conjunction = all_of(*(clauses[i] for i in order))
        context = EvalContext(discards_falsity=True)
        assert conjunction.eq(False).evaluate(context).confidence is HEURISTIC
        assert conjunction.ne(True).evaluate(context).confidence is HEURISTIC

    @pytest.mark.parametrize("order", list(itertools.permutations(range(2))))
    def test_a_rejected_conjunction_cannot_launder_a_guess_to_declared(self, order):
        # The end-to-end shape the fix is about: a cheap DECLARED field read
        # written next to a trait read resting on a guess. Under a short
        # circuit the field-first spelling never performs the trait read and
        # would report DECLARED for an answer that rests on it.
        clauses = [
            row.kind.eq("class"),
            trait_of("t").value.eq("list"),
        ]
        context = EvalContext(
            fields={"kind": "variable"},
            traits={"t": trait("list", HEURISTIC)},
            discards_falsity=True,
        )
        node = not_(all_of(*(clauses[i] for i in order))).evaluate(context)
        assert node.value is True
        assert node.confidence is HEURISTIC

    @pytest.mark.parametrize("order", list(itertools.permutations(range(2))))
    def test_a_negated_conjunction_runs_every_opaque_body(self, order):
        # Observable proof that the skipped operand really is evaluated, not
        # merely accounted for: the opaque body runs, in written order.
        calls: list[str] = []

        def recorder(label, result, reads):
            @opaque(label, reads=reads)
            def _predicate(_row, label=label, result=result):
                calls.append(label)
                return result

            return _predicate

        clauses = [
            recorder("cheap", False, ("name",)),
            recorder("guessy", True, ("trait:t",)),
        ]
        written = [clauses[i] for i in order]
        node = not_(all_of(*written)).evaluate(
            EvalContext(traits={"t": trait("list", HEURISTIC)}, discards_falsity=True)
        )
        assert node.value is True
        assert node.confidence is HEURISTIC
        assert calls == [c.name for c in written]

    def test_the_why_node_reports_every_read_it_met_over(self):
        # Self-consistency of the emitted tree under negation: the reads the
        # node lists and the confidence it claims come from the same set of
        # operands, so a consumer can check the claim.
        node = not_(
            all_of(row.kind.eq("class"), trait_of("t").value.eq("list"))
        ).evaluate(
            EvalContext(
                fields={"kind": "variable"},
                traits={"t": trait("list", HEURISTIC)},
                discards_falsity=True,
            )
        )
        assert node.reads == frozenset({"field:kind", "trait:t"})
        assert node.confidence is HEURISTIC

    def test_the_licence_is_withdrawn_for_a_whole_subtree_not_one_level(self):
        # not_(any_of(all_of(...))): the conjunction is two levels below the
        # node that consumes its falsity, and must still run every operand.
        expr = not_(any_of(all_of(Const(False, DECLARED), Const(True, HEURISTIC))))
        node = expr.evaluate(EvalContext(discards_falsity=True))
        assert node.value is True
        assert node.confidence is HEURISTIC

    def test_a_matching_conjunction_is_unaffected_by_the_licence(self):
        # Control: every operand ran either way, so the two contexts agree.
        expr = all_of(Const(True, DECLARED), Const(True, INFERRED))
        assert expr.evaluate(EvalContext()).confidence is INFERRED
        assert expr.evaluate(EvalContext(discards_falsity=True)).confidence is INFERRED


# --------------------------------------------------------------------------
# Leaves: field reads, projections, comparisons
# --------------------------------------------------------------------------


class TestLeaves:
    """Model reads are DECLARED; absent facts degrade rather than raise."""

    def test_a_present_field_reads_declared(self):
        node = row.name.evaluate(EvalContext(fields={"name": "items"}))
        assert node.value == "items"
        assert node.confidence is DECLARED
        assert node.reads == frozenset({"field:name"})

    def test_an_absent_field_reads_none_at_unknown_without_raising(self):
        node = row.nope.evaluate(EvalContext(fields={"name": "items"}))
        assert node.value is None
        assert node.confidence is UNKNOWN

    def test_an_absent_field_drags_a_conjunction_to_unknown(self):
        node = (row.name.eq("items") & row.nope.eq("x")).evaluate(
            EvalContext(fields={"name": "items"})
        )
        assert node.value is False
        assert node.confidence is UNKNOWN

    def test_an_absent_attribute_degrades_to_unknown(self):
        node = trait_of("t").value.attr("nope").evaluate(ctx(t=trait("list", DECLARED)))
        assert node.value is None
        assert node.confidence is UNKNOWN

    def test_comparisons_pass_evidence_through_unchanged(self):
        context = ctx(t=trait("list", HEURISTIC))
        for expr in (
            trait_of("t").value.eq("list"),
            trait_of("t").value.ne("tuple"),
            trait_of("t").value.is_in("list", "set"),
            trait_of("t").value.matches("li*"),
            trait_of("t").value.startswith("li"),
            trait_of("t").value.is_true(),
        ):
            node = expr.evaluate(context)
            assert node.value is True, expr
            assert node.confidence is HEURISTIC, expr

    def test_glob_matching_is_case_sensitive_and_non_strings_never_match(self):
        assert row.name.matches("LI*").evaluate(
            EvalContext(fields={"name": "list"})
        ).value is False
        assert row.name.startswith("li").evaluate(
            EvalContext(fields={"name": 3})
        ).value is False

    def test_the_row_proxy_refuses_dunder_names(self):
        with pytest.raises(AttributeError):
            row.__deepcopy__  # noqa: B018

    def test_expression_nodes_are_hashable_because_eq_is_not_overloaded(self):
        # Overloading __eq__ to build a Compare node would drop __hash__ and
        # make `assert node == expected` vacuously truthy.
        node = row.name.eq("items")
        assert {node, row.name.eq("items")} == {node}
        assert isinstance(node == row.name.eq("items"), bool)


# --------------------------------------------------------------------------
# The escape hatch
# --------------------------------------------------------------------------


class TestOpaque:
    """Fork #9: opacity is permitted only where it is declared."""

    def test_reads_is_mandatory_and_keyword_only(self):
        with pytest.raises(TypeError):
            opaque("nope")  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            opaque("nope", ("name",))  # type: ignore[misc]

    def test_empty_reads_is_a_loud_refusal_naming_the_grammar(self):
        with pytest.raises(OpaquePredicateError) as excinfo:
            opaque("nope", reads=())
        err = excinfo.value
        assert err.code == "opaque-predicate"
        assert "declares no reads" in str(err)
        assert "trait:" in str(err)
        assert "project:" in str(err)

    def test_a_prefix_without_a_name_is_refused(self):
        with pytest.raises(OpaquePredicateError):
            opaque("nope", reads=("trait:",))
        with pytest.raises(OpaquePredicateError):
            opaque("nope", reads=("project:",))
        with pytest.raises(OpaquePredicateError):
            opaque("nope", reads=("  ",))

    def test_the_body_sees_row_fields_by_attribute(self):
        @opaque("leading-underscore", reads=("name",))
        def leading_underscore(candidate):
            return candidate.name.startswith("_")

        assert leading_underscore.evaluate(
            EvalContext(fields={"name": "_hidden"})
        ).value is True
        assert leading_underscore.evaluate(
            EvalContext(fields={"name": "shown"})
        ).value is False

    def test_field_only_reads_report_declared(self):
        @opaque("always", reads=("name",))
        def always(_candidate):
            return True

        node = always.evaluate(EvalContext(fields={"name": "x"}))
        assert node.confidence is DECLARED
        assert node.reads == frozenset({"name"})
        assert node.detail["declared_reads"] == ["name"]

    def test_a_declared_trait_read_lowers_the_opaques_evidence(self):
        @opaque("looks-listy", reads=("trait:t",))
        def looks_listy(_candidate):
            return True

        assert looks_listy.evaluate(ctx(t=trait("list", INFERRED))).confidence is INFERRED
        assert looks_listy.evaluate(ctx(t=trait("list", HEURISTIC))).confidence is HEURISTIC

    def test_a_rejecting_opaque_still_meets_its_declared_trait_reads(self):
        # The tempting laziness -- skip the traits for a row the body rejected,
        # since "a conjunction containing it already went false" -- is unsound:
        # not_() consumes the rejection, so the evidence is load bearing on
        # both branches. Reporting DECLARED here would launder a guess.
        @opaque("never", reads=("trait:t",))
        def never(_candidate):
            return False

        node = never.evaluate(ctx(t=trait("list", HEURISTIC)))
        assert node.value is False
        assert node.confidence is HEURISTIC

    def test_negating_a_rejecting_opaque_cannot_launder_evidence_to_declared(self):
        @opaque("never", reads=("trait:t",))
        def never(_candidate):
            return False

        negated = not_(never).evaluate(ctx(t=trait("list", HEURISTIC)))
        assert negated.value is True
        assert negated.confidence is HEURISTIC

        unknowable = not_(never).evaluate(ctx(t=trait(None, UNKNOWN)))
        assert unknowable.value is True
        assert unknowable.confidence is UNKNOWN

    def test_comparing_a_rejecting_opaque_cannot_launder_evidence_either(self):
        @opaque("never", reads=("trait:t",))
        def never(_candidate):
            return False

        assert never.eq(False).evaluate(ctx(t=trait("x", INFERRED))).confidence is INFERRED
        assert never.ne(True).evaluate(ctx(t=trait("x", INFERRED))).confidence is INFERRED

    def test_the_why_node_never_names_a_read_it_did_not_account_for(self):
        # Self-consistency of the emitted tree: a node that lists 'trait:t' in
        # reads must have that trait's confidence in its own meet, whichever
        # way the body went. Otherwise a consumer reading the tree cannot tell
        # the claim is over-stated.
        @opaque("never", reads=("trait:t",))
        def never(_candidate):
            return False

        @opaque("always", reads=("trait:t",))
        def always(_candidate):
            return True

        for node in (
            never.evaluate(ctx(t=trait("x", HEURISTIC))),
            always.evaluate(ctx(t=trait("x", HEURISTIC))),
        ):
            assert node.reads == frozenset({"trait:t"})
            assert node.confidence is HEURISTIC

    def test_an_opaque_naming_an_unregistered_trait_is_loud_even_when_rejecting(self):
        @opaque("never", reads=("trait:absent",))
        def never(_candidate):
            return False

        with pytest.raises(UnknownTraitError):
            never.evaluate(EvalContext())

    def test_a_matching_opaque_naming_an_unregistered_trait_is_loud(self):
        @opaque("always", reads=("trait:absent",))
        def always(_candidate):
            return True

        with pytest.raises(UnknownTraitError):
            always.evaluate(EvalContext())

    def test_declared_reads_drive_reach(self):
        @opaque("local", reads=("name",))
        def local(_candidate):
            return True

        @opaque("global", reads=("project:importers",))
        def global_(_candidate):
            return True

        assert local.reach is Reach.FILE
        assert global_.reach is Reach.PROJECT
        assert (local & global_).reach is Reach.PROJECT
        assert (local & row.name.eq("x")).reach is Reach.FILE


# --------------------------------------------------------------------------
# Derived reach and read introspection
# --------------------------------------------------------------------------


class TestDerivedFacts:
    """Reach, field names and trait names are derived from the tree, never declared."""

    def test_a_pure_model_expression_reaches_file(self):
        assert (row.kind.eq("variable") & row.name.startswith("_")).reach is Reach.FILE

    def test_trait_reads_reach_file(self):
        assert trait_of(TYPE_ANNOTATION).at(INFERRED).reach is Reach.FILE
        assert TUPLE_CANDIDATE.reach is Reach.FILE

    def test_reach_cannot_be_declared_on_an_expression(self):
        expr = row.name.eq("x")
        assert isinstance(type(expr).reach, property)
        with pytest.raises(AttributeError):
            expr.reach = Reach.PROJECT  # type: ignore[misc]

    def test_field_names_and_trait_names_collect_the_whole_tree(self):
        assert TUPLE_CANDIDATE.field_names == frozenset({"kind", "scope_kind"})
        assert TUPLE_CANDIDATE.trait_names == frozenset({TYPE_ANNOTATION, VARIABLE_MUTATION})

    def test_reads_tokens_are_prefixed_by_substrate(self):
        node = TUPLE_CANDIDATE.evaluate(prefer_tuple_context())
        assert node.reads == frozenset({
            "field:kind",
            "field:scope_kind",
            f"trait:{TYPE_ANNOTATION}",
            f"trait:{VARIABLE_MUTATION}",
        })


# --------------------------------------------------------------------------
# The derivation node
# --------------------------------------------------------------------------


class TestDerivationNode:
    """The tree ``--why`` will serialize, and the guardrail it must respect."""

    def test_a_derivation_mirrors_the_expression_tree(self):
        node = TUPLE_CANDIDATE.evaluate(prefer_tuple_context())
        assert isinstance(node, Derivation)
        assert node.op == "all_of"
        assert len(node.inputs) == len(TUPLE_CANDIDATE.operands)

    def test_derivation_detail_is_read_only(self):
        node = TUPLE_CANDIDATE.evaluate(prefer_tuple_context())
        with pytest.raises(TypeError):
            node.detail["short_circuited"] = True  # type: ignore[index]

    def test_no_node_carries_a_trait_provenance_string(self):
        # analysis/traits.py forbids Trait.provenance from reaching output;
        # the derivation tree is a different object and never copies it in.
        secret = "SENTINEL-PROVENANCE-STRING"
        context = prefer_tuple_context()
        context = EvalContext(
            fields=context.fields,
            traits={
                name: trait(t.value, t.confidence, provenance=secret)
                for name, t in context.traits.items()
            },
        )
        seen: list[Derivation] = []
        stack = [TUPLE_CANDIDATE.evaluate(context)]
        while stack:
            current = stack.pop()
            seen.append(current)
            stack.extend(current.inputs)
        assert len(seen) > 1
        for current in seen:
            assert secret not in repr(current.value)
            assert secret not in repr(dict(current.detail))
            assert secret not in repr(current.reads)


def test_expr_base_evaluate_is_abstract():
    """The base node has no evaluation of its own."""
    with pytest.raises(NotImplementedError):
        Expr().evaluate(EvalContext())
