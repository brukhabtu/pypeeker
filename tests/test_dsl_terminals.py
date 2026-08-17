"""Mutation terminals: fork #10's closed kind set, fork #5's derived id, fork #2's floor.

The three forks phase 4 settles, each tested where it is actually decided
rather than where it is described. The most load-bearing test in the file is
:func:`test_planned_kinds_is_exactly_the_registry_refactor_holds` — ``dsl`` may
not import ``refactor``, so :data:`pypeeker.dsl.PLANNED_KINDS` is a literal
copy of that table, and this is the only thing in the tree that stops the copy
from rotting into a mutation nobody can plan.
"""

import pytest

from pypeeker.check.builtin.docstring_drift import DOCSTRING_DRIFT
from pypeeker.check.builtin.star_imports import STAR_IMPORTS
from pypeeker.check.builtin.unused_imports import UNUSED_IMPORTS
from pypeeker.dsl import (
    DEMOTE,
    MUTATIONS,
    PLANNED_KINDS,
    REMOVE_IMPORT,
    RENAME_DOCSTRING_PARAM,
    REWRITE_STAR_IMPORT,
    Anchor,
    AnchorKind,
    Application,
    FromAnchor,
    FromRow,
    Match,
    Mutation,
    MutationPreconditionError,
    Precondition,
    UnknownFieldError,
    UnplannableMutationError,
    all_of,
    not_,
    opaque,
    row,
    symbols,
    trait_of,
)
from pypeeker.intents import (
    ChangeVisibilityIntent,
    ExtractMethodIntent,
    Intent,
    RemoveImportIntent,
    RenameIntent,
)
from pypeeker.dsl.terminals import _required_params
from pypeeker.models import Confidence
from pypeeker.refactor import registry


def a_match(anchor_id="pkg.app:thing", *, confidence=Confidence.DECLARED, **fields):
    """One hand-built row, the shape a selection would have produced."""
    return Match(
        anchor=Anchor(kind=AnchorKind.SYMBOL, id=anchor_id),
        fields=fields,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# fork #10: a mutation may only name a planner kind that exists
# ---------------------------------------------------------------------------


def test_planned_kinds_is_exactly_the_registry_refactor_holds():
    # THE guard for fork #10. `dsl` may not import `refactor`, so PLANNED_KINDS
    # is a literal copy of refactor/registry.py's table; this test is the one
    # place the two meet, and it asserts equality in BOTH directions — a
    # membership check would miss a planner added to the registry and never
    # mirrored, which is how a mutation would go unwritable but unnoticed.
    assert PLANNED_KINDS == frozenset(registry._REGISTRY)


def test_a_mutation_naming_a_kind_no_planner_registers_is_refused_at_construction():
    class UnplannedIntent(RemoveImportIntent):
        kind = "teleport-symbol"

    with pytest.raises(UnplannableMutationError) as caught:
        Mutation(
            name="teleport",
            intent=UnplannedIntent,
            params={"symbol_id": FromRow("symbol_id"), "name": FromRow("name")},
            floor=Confidence.DECLARED,
        )
    assert "teleport-symbol" in str(caught.value)
    assert "rewrite-star-import" in str(caught.value)
    assert caught.value.code == "unplannable-mutation"


def test_the_refusal_names_every_planner_kind_so_the_author_can_pick_one():
    class UnplannedIntent(RemoveImportIntent):
        kind = "teleport-symbol"

    with pytest.raises(UnplannableMutationError) as caught:
        Mutation(
            name="teleport",
            intent=UnplannedIntent,
            params={},
            floor=Confidence.DECLARED,
        )
    assert set(caught.value.valid) == set(PLANNED_KINDS)


def test_the_abstract_intent_base_is_not_a_plannable_mutation_target():
    with pytest.raises(UnplannableMutationError):
        Mutation(name="anything", intent=Intent, params={}, floor=Confidence.DECLARED)


def test_something_that_is_not_an_intent_class_at_all_is_refused():
    with pytest.raises(UnplannableMutationError):
        Mutation(name="anything", intent=str, params={}, floor=Confidence.DECLARED)


def test_a_kind_whose_planner_exists_constructs_fine():
    # ExtractMethodIntent's planner is registered even though no rule names it;
    # fork #10 constrains the kind, not whether anybody uses it yet.
    assert ExtractMethodIntent.kind in PLANNED_KINDS


# ---------------------------------------------------------------------------
# fork #5: the derived id, and the absence of an override
# ---------------------------------------------------------------------------


def test_intent_id_is_origin_mutation_anchor_and_nothing_else():
    assert REMOVE_IMPORT.intent_id("unused-imports", "pkg.app:json") == (
        "unused-imports:remove:pkg.app:json"
    )


def test_setting_intent_id_in_params_is_refused_as_an_override():
    with pytest.raises(UnplannableMutationError) as caught:
        Mutation(
            name="rename",
            intent=RenameIntent,
            params={"intent_id": "hand-written", "symbol_id": FromAnchor()},
            floor=Confidence.DECLARED,
        )
    message = str(caught.value)
    assert "intent_id" in message
    assert "Fork #5" in message


def test_a_params_key_the_intent_does_not_declare_is_refused():
    with pytest.raises(UnplannableMutationError) as caught:
        Mutation(
            name="remove",
            intent=RemoveImportIntent,
            params={"symbol_id": FromAnchor(), "modul": "typo"},
            floor=Confidence.DECLARED,
        )
    assert "'modul'" in str(caught.value)


def test_the_three_frozen_check_remedy_ids_are_reproduced_character_for_character():
    # The whole reason fork #5's derivation lands in phase 4 rather than at the
    # flip: with the mutation names `remove`, `rewrite` and `rename-param`, the
    # derived id IS the frozen id, so the fix-level differential can compare
    # fix ids exactly while both engines still exist.
    assert REMOVE_IMPORT.intent_id(UNUSED_IMPORTS, "pkg.app:json") == (
        f"{UNUSED_IMPORTS}:remove:pkg.app:json"
    )
    assert REWRITE_STAR_IMPORT.intent_id(STAR_IMPORTS, "pkg.app:*") == (
        f"{STAR_IMPORTS}:rewrite:pkg.app:*"
    )
    # drift_rows anchors on `<function id>:<param>`, so the ghost name is
    # already part of the anchor and the derived id needs no fourth segment.
    assert RENAME_DOCSTRING_PARAM.intent_id(DOCSTRING_DRIFT, "pkg.app:f:old") == (
        f"{DOCSTRING_DRIFT}:rename-param:pkg.app:f:old"
    )


# ---------------------------------------------------------------------------
# fork #2: the floor is an attribute of the value, read in one place
# ---------------------------------------------------------------------------


def test_a_row_below_the_floor_yields_no_intent_and_says_why():
    decision = REMOVE_IMPORT.decide(
        UNUSED_IMPORTS,
        a_match(confidence=Confidence.HEURISTIC, symbol_id="pkg.app:json", name="json"),
    )
    assert decision.intent is None
    assert decision.reason == "below-floor"


def test_a_row_at_the_floor_yields_the_intent():
    decision = REMOVE_IMPORT.decide(
        UNUSED_IMPORTS,
        a_match("pkg.app:json", confidence=Confidence.DECLARED,
                symbol_id="pkg.app:json", name="json"),
    )
    assert decision.reason == ""
    assert decision.intent == RemoveImportIntent(
        "unused-imports:remove:pkg.app:json", symbol_id="pkg.app:json", name="json"
    )


def test_demote_admits_inferred_where_the_declared_floor_would_not():
    # The two floors are genuinely different numbers on the same lattice, which
    # is what makes "the floor is an attribute of the value" observable at all.
    inferred = a_match(confidence=Confidence.INFERRED, name="thing")
    assert DEMOTE.decide("cli", inferred).intent is not None
    assert REMOVE_IMPORT.floor is Confidence.DECLARED
    assert DEMOTE.floor is Confidence.INFERRED


def test_unknown_is_below_the_inferred_floor():
    unknown = a_match(confidence=Confidence.UNKNOWN, name="thing")
    assert DEMOTE.decide("cli", unknown).reason == "below-floor"


# ---------------------------------------------------------------------------
# preconditions: pointwise, ordered, named by the reason they report
# ---------------------------------------------------------------------------


def test_the_first_failing_precondition_is_the_reported_reason():
    # dunder-or-main is written before already-private precisely so a dunder
    # reports the more precise reason, mirroring the frozen pre-filter's own
    # ordering comment. A dunder also starts with an underscore, so if the
    # order flipped this would report "already-private".
    assert DEMOTE.decide("cli", a_match(name="__init__")).reason == "dunder-or-main"
    assert DEMOTE.decide("cli", a_match(name="main")).reason == "dunder-or-main"
    assert DEMOTE.decide("cli", a_match(name="_helper")).reason == "already-private"


def test_the_two_clause_dunder_spelling_catches_bare_underscore_names():
    # The phase-3d ledger note: `matches("__*__")` needs four characters, and
    # get_visibility classifies `__` and `___` as PRIVATE rather than DUNDER,
    # so they reach the clause. The frozen _demote_candidates skips both.
    assert DEMOTE.decide("cli", a_match(name="__")).reason == "dunder-or-main"
    assert DEMOTE.decide("cli", a_match(name="___")).reason == "dunder-or-main"


def test_a_precondition_reading_a_trait_is_refused_at_construction():
    with pytest.raises(MutationPreconditionError) as caught:
        Mutation(
            name="demote",
            intent=ChangeVisibilityIntent,
            params={"symbol_id": FromAnchor(), "direction": "demote"},
            floor=Confidence.DECLARED,
            preconditions=(
                Precondition("pure", trait_of("purity").value.eq("pure")),
            ),
        )
    assert caught.value.code == "mutation-precondition"
    assert "purity" in str(caught.value)


# ---------------------------------------------------------------------------
# the application operator, and the field validation both entry points share
# ---------------------------------------------------------------------------


def test_apply_refuses_a_mutation_reading_a_field_the_selection_does_not_expose():
    # Without this the symptom is silent: a missing field reads as None at
    # UNKNOWN, the precondition goes false, and the repair vanishes.
    with pytest.raises(UnknownFieldError) as caught:
        symbols().apply(RENAME_DOCSTRING_PARAM)
    assert caught.value.field == "counterpart"
    assert caught.value.required_by == "rename-param"


def test_apply_refuses_a_field_a_project_stage_dropped():
    with pytest.raises(UnknownFieldError):
        symbols().project("symbol_id").apply(DEMOTE)


def test_apply_takes_exactly_one_argument_and_no_options():
    # The application operator takes no options, by panel convergence: the
    # floor and the guards are attributes of the mutation value. If a keyword
    # ever appears here, fork #2 has been reopened.
    import inspect

    signature = inspect.signature(type(symbols()).apply)
    assert [p.name for p in signature.parameters.values()] == ["self", "mutation"]


def test_a_mutation_reading_only_the_anchor_needs_no_field_at_all():
    anchor_only = Mutation(
        name="demote",
        intent=ChangeVisibilityIntent,
        params={"symbol_id": FromAnchor(), "direction": "demote"},
        floor=Confidence.DECLARED,
    )
    assert anchor_only.field_reads == frozenset()
    assert symbols().project("symbol_id").apply(anchor_only) is not None


def test_field_reads_covers_params_and_preconditions_alike():
    assert RENAME_DOCSTRING_PARAM.field_reads == frozenset({
        "symbol_id",
        "param",
        "counterpart",
        "detected_style",
        "drift",
        "ghost_count",
        "missing_count",
    })


def test_a_from_row_param_naming_an_absent_field_raises_rather_than_passing_none():
    # Unreachable through either application entry point, both of which
    # validate up front — but the terminal must not quietly build an intent
    # with a None anchor if one is ever reached another way.
    reads_a_field = Mutation(
        name="remove",
        intent=RemoveImportIntent,
        params={"symbol_id": FromRow("symbol_id"), "name": FromRow("name")},
        floor=Confidence.DECLARED,
    )
    with pytest.raises(KeyError):
        reads_a_field.decide(UNUSED_IMPORTS, a_match(symbol_id="pkg.app:json"))


def test_params_and_preconditions_are_frozen_after_construction():
    with pytest.raises(TypeError):
        REMOVE_IMPORT.params["name"] = FromAnchor()


# ---------------------------------------------------------------------------
# the star-imports mutation: the frozen guard, split between floor and guards
# ---------------------------------------------------------------------------


def test_star_rewrite_is_withheld_from_a_target_that_is_not_indexed():
    decision = REWRITE_STAR_IMPORT.decide(
        STAR_IMPORTS,
        a_match(indexed=False, name_count=0, imported_from="other"),
    )
    assert decision.reason == "target-not-indexed"


def test_star_rewrite_is_withheld_when_the_star_supplies_no_used_name():
    decision = REWRITE_STAR_IMPORT.decide(
        STAR_IMPORTS,
        a_match(indexed=True, name_count=0, imported_from="other"),
    )
    assert decision.reason == "no-used-names"


def test_star_rewrite_quotes_the_anchor_and_the_target_module():
    decision = REWRITE_STAR_IMPORT.decide(
        STAR_IMPORTS,
        a_match("pkg.app:*", indexed=True, name_count=2, imported_from="pkg.target"),
    )
    assert decision.intent.intent_id == "star-imports:rewrite:pkg.app:*"
    assert decision.intent.module == "pkg.target"
    assert decision.intent.symbol_id == "pkg.app:*"


# ---------------------------------------------------------------------------
# the rename-param mutation: the frozen `renameable` guard as preconditions
# ---------------------------------------------------------------------------


def drift_match(**overrides):
    fields = {
        "symbol_id": "pkg.app:f",
        "param": "old",
        "counterpart": "new",
        "detected_style": "google",
        "drift": "ghost",
        "ghost_count": 1,
        "missing_count": 1,
    }
    fields.update(overrides)
    return a_match("pkg.app:f:" + fields["param"], **fields)


def test_rename_param_builds_the_frozen_intent_for_the_unambiguous_rename():
    decision = RENAME_DOCSTRING_PARAM.decide(DOCSTRING_DRIFT, drift_match())
    assert decision.intent.intent_id == "docstring-drift:rename-param:pkg.app:f:old"
    assert decision.intent.old_param == "old"
    assert decision.intent.new_param == "new"
    assert decision.intent.style == "google"


def test_rename_param_is_withheld_from_the_missing_direction():
    # The `require-complete` half of the rule declares the same repair; the
    # precondition is what silences it, not a missing declaration.
    assert RENAME_DOCSTRING_PARAM.decide(
        DOCSTRING_DRIFT, drift_match(drift="missing")
    ).reason == "not-a-ghost"


def test_rename_param_is_withheld_when_the_drift_is_not_one_for_one():
    assert RENAME_DOCSTRING_PARAM.decide(
        DOCSTRING_DRIFT, drift_match(ghost_count=2)
    ).reason == "not-the-unambiguous-rename"
    assert RENAME_DOCSTRING_PARAM.decide(
        DOCSTRING_DRIFT, drift_match(missing_count=0, counterpart="")
    ).reason == "not-the-unambiguous-rename"


# ---------------------------------------------------------------------------
# one operation is one mutation object
# ---------------------------------------------------------------------------


def test_every_named_mutation_appears_in_the_registry_under_its_own_name():
    from pypeeker.dsl import MUTATIONS

    for name, mutation in MUTATIONS.items():
        assert mutation.name == name
    assert set(MUTATIONS) == {
        "remove",
        "rewrite",
        "rename-param",
        "tuplify",
        "delete",
        "demote",
    }


def test_no_two_named_mutations_share_an_intent_kind():
    from pypeeker.dsl import MUTATIONS

    kinds = [mutation.intent.kind for mutation in MUTATIONS.values()]
    assert len(set(kinds)) == len(kinds)


def test_a_precondition_expression_stays_ordinary_dsl_and_composes():
    guard = Precondition("composed", not_(all_of(row.a.is_true(), row.b.is_true())))
    mutation = Mutation(
        name="demote",
        intent=ChangeVisibilityIntent,
        params={"symbol_id": FromAnchor(), "direction": "demote"},
        floor=Confidence.UNKNOWN,
        preconditions=(guard,),
    )
    assert mutation.decide("cli", a_match(a=True, b=True)).reason == "composed"
    assert mutation.decide("cli", a_match(a=True, b=False)).intent is not None


# ---------------------------------------------------------------------------
# construction-time completeness: the promise a module-level value makes
# ---------------------------------------------------------------------------


def test_a_mutation_omitting_a_required_param_is_refused_at_construction():
    # Not merely "loud" — loud EARLY. A mutation is a module-level value, so
    # the alternative is a TypeError raised on the first row that reaches
    # decide(), long after the value has been imported and published.
    with pytest.raises(UnplannableMutationError) as caught:
        Mutation(
            name="halfbaked",
            intent=RemoveImportIntent,
            params={"symbol_id": FromRow("symbol_id")},
            floor=Confidence.DECLARED,
        )
    assert "'name'" in str(caught.value)


def test_a_param_with_a_default_is_not_required():
    # The check is "no default", not "not passed": RewriteStarImportIntent's
    # optional keywords must stay optional or every shipped mutation breaks.
    mutation = Mutation(
        name="remove",
        intent=RemoveImportIntent,
        params={"symbol_id": FromAnchor(), "name": FromRow("name")},
        floor=Confidence.DECLARED,
    )
    assert mutation.intent is RemoveImportIntent


def test_every_shipped_mutation_survives_its_own_completeness_check():
    # The guard's premise: it would be worthless if the shipped values did not
    # themselves pass it. Importing the module already ran the check on each,
    # so this pins that the registry is non-empty and self-consistent.
    assert MUTATIONS
    for name, mutation in MUTATIONS.items():
        assert mutation.name == name
        assert not _required_params(mutation.intent) - set(mutation.params)


# ---------------------------------------------------------------------------
# the field-visibility check cannot be walked around
# ---------------------------------------------------------------------------


def test_constructing_an_application_directly_still_validates_its_fields():
    # Application is exported and directly constructible, so validating only in
    # Selection.apply() would leave one door unwatched — and through it the
    # guard reads None at UNKNOWN for every row, goes false, and intents()
    # returns () with nothing raising.
    with pytest.raises(UnknownFieldError) as caught:
        Application(selection=symbols().project("symbol_id"), mutation=REMOVE_IMPORT)
    assert "'name'" in str(caught.value)


def test_an_opaque_precondition_declaring_a_real_field_is_validated():
    # _field_reads ignores opaque reads= tokens on purpose, so a mutation
    # guarded only by an opaque used to pass the visibility check and then
    # refuse every row.
    @opaque("public-name", reads=("name",))
    def _is_public(record):
        return not record.name.startswith("_")

    mutation = Mutation(
        name="remove",
        intent=RemoveImportIntent,
        params={"symbol_id": FromAnchor(), "name": FromRow("name")},
        floor=Confidence.DECLARED,
        preconditions=(Precondition("public-name", _is_public),),
    )
    assert "name" in mutation.field_reads


def test_a_free_form_opaque_read_is_still_not_a_field_name():
    # The other half, and the reason the check intersects with the universes'
    # vocabulary rather than taking every token: an honest prose declaration
    # must not become an authoring error.
    @opaque("prose", reads=("the symbol's own name",))
    def _always(record):
        del record
        return True

    mutation = Mutation(
        name="remove",
        intent=RemoveImportIntent,
        params={"symbol_id": FromAnchor(), "name": FromRow("name")},
        floor=Confidence.DECLARED,
        preconditions=(Precondition("prose", _always),),
    )
    assert mutation.field_reads == frozenset({"name"})
