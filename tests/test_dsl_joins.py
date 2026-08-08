"""Project-reach expression surface: semi-joins, project columns, weakening, expression rhs.

Everything the visibility / reference-counting rule family needs beyond the
phase-2 grammar, pinned in isolation before any rule is written on top of it.
Every assertion is about observable behaviour — which rows come back, what
confidence they carry, the exact wording of a refusal, how many times a
corpus-wide table is actually computed — never about a name existing.
"""

import pytest

from pypeeker.analysis import TYPE_ANNOTATION
from pypeeker.dsl import (
    COLUMNS,
    DEFINITION_ID,
    DEFINITION_KIND,
    DEFINITION_MODULE,
    USAGE_ORIGINS,
    Const,
    Corpus,
    DslError,
    EvalContext,
    Reach,
    ReachError,
    UnknownExpressionError,
    UnknownFieldError,
    all_of,
    column_of,
    corpus_set,
    in_set,
    not_,
    projected_set,
    references,
    row,
    symbols,
    trait,
    trait_of,
    universe_fields,
    weakened_when,
)
from pypeeker.models import Confidence, SymbolKind

BARREL = "from pkg.mod import Thing\n"

MOD = """\
class Thing:
    pass


def helper():
    return 1
"""

APP = """\
import json
from pkg import Thing


def use():
    return len(Thing())
"""


@pytest.fixture
def corpus(indexed_project):
    """A package that re-exports through its barrel, plus an outside consumer.

    ``pkg/__init__.py`` re-exports ``pkg.mod:Thing``; ``app.py`` imports it
    through that barrel and calls it, and also imports stdlib ``json`` whose
    definition is outside the corpus.
    """
    _, store = indexed_project({
        "pkg/__init__.py": BARREL,
        "pkg/mod.py": MOD,
        "app.py": APP,
    })
    return Corpus(store)


class CountingCorpus(Corpus):
    """A corpus recording the memo key of every table actually computed."""

    def __init__(self, store, src_roots=()):
        super().__init__(store, src_roots)
        self.built = []

    def materialize(self, key, factory):
        """Record ``key`` when the factory actually runs, then delegate."""

        def _counted():
            self.built.append(key)
            return factory()

        return super().materialize(key, _counted)


@pytest.fixture
def counting_corpus(indexed_project):
    """The same corpus, instrumented to count materializations."""
    _, store = indexed_project({
        "pkg/__init__.py": BARREL,
        "pkg/mod.py": MOD,
        "app.py": APP,
    })
    return CountingCorpus(store)


def barrel_exports():
    """The barrel-export set: definitions re-exported by any ``__init__.py``."""
    return projected_set(
        "barrel-exports",
        symbols()
        .where(all_of(row.file_path.matches("*__init__.py"), row.kind.eq(SymbolKind.IMPORT)))
        .follow("definition")
        .project("symbol_id"),
    )


# ---------------------------------------------------------------------------
# ProjectedSet: one column, refused otherwise
# ---------------------------------------------------------------------------


def test_projected_set_is_the_column_over_the_whole_corpus(corpus):
    assert barrel_exports().values(corpus) == frozenset({"pkg.mod:Thing"})


def test_projected_set_column_is_the_projected_field():
    assert barrel_exports().column == "symbol_id"


def test_projected_set_refuses_a_selection_that_is_not_projected_to_one_column():
    with pytest.raises(DslError) as excinfo:
        projected_set("everything", symbols())
    message = str(excinfo.value)
    assert "projected set 'everything'" in message
    assert f"{len(universe_fields('symbols'))} are visible" in message
    assert ".project('<column>')" in message


def test_projected_set_refuses_a_two_column_projection():
    with pytest.raises(DslError) as excinfo:
        projected_set("pair", symbols().project("symbol_id", "name"))
    assert "2 are visible: name, symbol_id" in str(excinfo.value)


def test_projected_set_drops_rows_with_no_value_for_the_column(corpus):
    # ``imported_from`` is None for every non-import symbol, so only the two
    # import symbols contribute a key.
    imported = projected_set("imported-from", symbols().project("imported_from"))
    assert imported.values(corpus) == frozenset({"pkg.mod.Thing", "pkg.Thing", "json"})


# ---------------------------------------------------------------------------
# materialized once per run, keyed structurally
# ---------------------------------------------------------------------------


def test_a_projected_set_is_computed_once_however_many_times_it_is_read(counting_corpus):
    exports = barrel_exports()
    exports.values(counting_corpus)
    exports.values(counting_corpus)
    exports.values(counting_corpus)
    assert len(counting_corpus.built) == 1


def test_two_independently_built_identical_sets_share_one_computation(counting_corpus):
    barrel_exports().values(counting_corpus)
    barrel_exports().values(counting_corpus)
    assert len(counting_corpus.built) == 1


def test_same_name_different_selection_does_not_share_a_cached_value(corpus):
    classes = projected_set(
        "shadowed", symbols().where(row.kind.eq(SymbolKind.CLASS)).project("symbol_id")
    )
    functions = projected_set(
        "shadowed", symbols().where(row.kind.eq(SymbolKind.FUNCTION)).project("symbol_id")
    )
    assert classes.values(corpus) == frozenset({"pkg.mod:Thing"})
    assert functions.values(corpus) == frozenset({"pkg.mod:helper", "app:use"})


def test_corpus_set_loader_runs_once(corpus):
    calls = []

    def _load(_corpus):
        calls.append(1)
        return {"pkg.mod:Thing"}

    recorded = corpus_set("recorded", ("baseline:symbols",), _load)
    assert recorded.values(corpus) == frozenset({"pkg.mod:Thing"})
    assert recorded.values(corpus) == frozenset({"pkg.mod:Thing"})
    assert len(calls) == 1


def test_corpus_set_refuses_an_empty_reads_declaration():
    with pytest.raises(DslError) as excinfo:
        corpus_set("recorded", (), lambda _corpus: ())
    assert "corpus set 'recorded' declares no reads" in str(excinfo.value)


# ---------------------------------------------------------------------------
# SemiJoin: reach, evidence, loudness
# ---------------------------------------------------------------------------


def test_semi_join_reaches_project_whatever_the_key_reads():
    assert in_set(row.symbol_id, barrel_exports()).reach is Reach.PROJECT


def test_semi_join_without_a_corpus_refuses_loudly_rather_than_answering_false():
    node = in_set(row.symbol_id, barrel_exports())
    with pytest.raises(ReachError) as excinfo:
        node.evaluate(EvalContext(fields={"symbol_id": "pkg.mod:Thing"}))
    assert excinfo.value.code == "reach-refused"
    assert "in_set(..., 'barrel-exports')" in str(excinfo.value)


def test_semi_join_filters_on_membership(corpus):
    exported = symbols().where(in_set(row.symbol_id, barrel_exports()))
    assert [match.anchor.id for match in exported.rows(corpus)] == ["pkg.mod:Thing"]


def test_semi_join_survives_negation_so_the_corpus_reaches_a_consuming_context(corpus):
    # not_() rebuilds the context through consuming_falsity(); if that dropped
    # the corpus the semi-join under it would raise ReachError instead of
    # answering.
    others = symbols().where(
        all_of(row.kind.eq(SymbolKind.CLASS), not_(in_set(row.symbol_id, barrel_exports())))
    )
    assert [match.anchor.id for match in others.rows(corpus)] == []


def test_semi_join_carries_the_keys_evidence_not_the_sets(corpus):
    node = in_set(Const("pkg.mod:Thing", Confidence.HEURISTIC), barrel_exports())
    derived = node.evaluate(EvalContext(corpus=corpus))
    assert derived.value is True
    assert derived.confidence is Confidence.HEURISTIC


def test_semi_join_declares_its_set_as_a_project_read(corpus):
    node = in_set(row.symbol_id, barrel_exports())
    derived = node.evaluate(
        EvalContext(fields={"symbol_id": "pkg.mod:Thing"}, corpus=corpus)
    )
    assert "project:set:barrel-exports" in derived.reads
    assert derived.detail["set"] == "barrel-exports"
    assert derived.detail["size"] == 1


def test_semi_join_is_refused_as_a_trait_because_its_reach_is_project():
    with pytest.raises(ReachError) as excinfo:
        trait("dsl-test-semi-join", in_set(row.symbol_id, barrel_exports()))
    assert "its reach is project" in str(excinfo.value)


# ---------------------------------------------------------------------------
# project columns
# ---------------------------------------------------------------------------


def test_definition_id_follows_a_barrel_re_export_without_leaving_the_row(corpus):
    through_barrel = symbols().where(
        all_of(row.symbol_id.eq("app:Thing"), column_of(DEFINITION_ID).eq("pkg.mod:Thing"))
    )
    assert [match.anchor.id for match in through_barrel.rows(corpus)] == ["app:Thing"]


def test_definition_kind_sees_through_the_import_chain(corpus):
    classes = symbols().where(column_of(DEFINITION_KIND).eq(SymbolKind.CLASS))
    assert [match.anchor.id for match in classes.rows(corpus)] == [
        "app:Thing",
        "pkg:Thing",
        "pkg.mod:Thing",
    ]


def test_a_definition_outside_the_corpus_is_unmatched(corpus):
    # ``len`` resolves to a builtin the corpus does not hold. The id column
    # still answers; the columns that need to *locate* the definition do not.
    builtin = column_of(DEFINITION_ID).eq("<builtins>.len")
    assert [m.fields["module"] for m in references().where(builtin).rows(corpus)] == ["app"]
    kinded = references().where(all_of(builtin, column_of(DEFINITION_KIND).ne(None)))
    assert [m.fields["module"] for m in kinded.rows(corpus)] == []


def test_an_unmatched_column_makes_not_true_which_is_why_locatability_comes_first(corpus):
    # UNMATCHED compares false against everything, so not_() over it is TRUE.
    # A rule asking "is the definition itself an import?" therefore has to
    # test locatability *before* negating the kind, or an unlocatable
    # definition sails through the negation.
    builtin = column_of(DEFINITION_ID).eq("<builtins>.len")
    flipped = references().where(
        all_of(builtin, not_(column_of(DEFINITION_KIND).eq(SymbolKind.IMPORT)))
    )
    assert [m.fields["module"] for m in flipped.rows(corpus)] == ["app"]
    guarded = references().where(
        all_of(
            builtin,
            column_of(DEFINITION_KIND).ne(None),
            not_(column_of(DEFINITION_KIND).eq(SymbolKind.IMPORT)),
        )
    )
    assert [m.fields["module"] for m in guarded.rows(corpus)] == []


def test_definition_module_is_the_module_declaring_the_definition(corpus):
    within = symbols().where(
        all_of(
            row.kind.eq(SymbolKind.IMPORT),
            column_of(DEFINITION_MODULE).is_within(row.module),
        )
    )
    # ``pkg:Thing`` re-exports its own submodule's class, so it is within.
    # ``app:Thing`` reaches across into pkg, so it is not. ``app:json`` is an
    # import the resolver cannot see past, and an unresolvable import resolves
    # to *itself* — so its definition module is its own, which is within.
    assert [match.anchor.id for match in within.rows(corpus)] == ["app:json", "pkg:Thing"]


def test_usage_origins_are_the_modules_referencing_the_definition(corpus):
    derived = column_of(USAGE_ORIGINS).evaluate(
        EvalContext(corpus=corpus, universe="symbols", subject=_symbol(corpus, "pkg.mod:Thing"))
    )
    assert derived.value == frozenset({"app"})
    assert derived.confidence is Confidence.DECLARED


def test_usage_origins_answers_used_from_elsewhere(corpus):
    elsewhere = symbols().where(
        all_of(
            row.kind.eq(SymbolKind.CLASS),
            column_of(USAGE_ORIGINS).any_other_than(row.module),
        )
    )
    assert [match.anchor.id for match in elsewhere.rows(corpus)] == ["pkg.mod:Thing"]


def test_usage_origins_over_a_reference_row_resolves_the_use_site(corpus):
    used = references().where(column_of(DEFINITION_ID).eq("pkg.mod:Thing"))
    assert [match.fields["module"] for match in used.rows(corpus)] == ["app"]


def test_a_column_reads_the_subject_not_the_projected_fields(corpus):
    # project() dropped symbol_id, so row.symbol_id is refused here...
    narrowed = symbols().project("name", "module")
    with pytest.raises(UnknownFieldError):
        narrowed.where(row.symbol_id.eq("app:Thing"))
    # ...but a column still answers, because it reads the row's model object.
    surviving = narrowed.where(column_of(DEFINITION_ID).eq("pkg.mod:Thing"))
    assert [match.fields["name"] for match in surviving.rows(corpus)] == [
        "Thing",
        "Thing",
        "Thing",
    ]


def test_column_reaches_project_and_reports_declared(corpus):
    assert column_of(DEFINITION_ID).reach is Reach.PROJECT
    derived = column_of(DEFINITION_ID).evaluate(
        EvalContext(corpus=corpus, universe="symbols", subject=_symbol(corpus, "app:Thing"))
    )
    assert derived.value == "pkg.mod:Thing"
    assert derived.confidence is Confidence.DECLARED
    assert "project:definition-id" in derived.reads


def test_column_without_a_corpus_refuses_loudly():
    with pytest.raises(ReachError) as excinfo:
        column_of(DEFINITION_ID).evaluate(EvalContext())
    assert "column_of('definition-id')" in str(excinfo.value)


def test_unknown_column_names_the_registered_ones():
    with pytest.raises(UnknownExpressionError) as excinfo:
        column_of("definition-rows")
    message = str(excinfo.value)
    assert "unknown expression 'definition-rows'" in message
    for name in COLUMNS:
        assert name in message


def test_a_column_is_refused_as_a_trait_because_its_reach_is_project():
    with pytest.raises(ReachError):
        trait("dsl-test-column", column_of(DEFINITION_ID).ne(None))


def _symbol(corpus, symbol_id):
    """The model Symbol behind ``symbol_id``, for evaluating a column pointwise."""
    found = corpus.locate(symbol_id)
    assert found is not None, symbol_id
    return found[0]


# ---------------------------------------------------------------------------
# Weaken
# ---------------------------------------------------------------------------


def test_weaken_reports_its_level_when_the_predicate_holds():
    derived = weakened_when(Const(True), Confidence.HEURISTIC).evaluate(EvalContext())
    assert derived.value is True
    assert derived.confidence is Confidence.HEURISTIC
    assert derived.detail["applied"] is True
    assert derived.detail["weakened_to"] == "heuristic"


def test_weaken_reports_declared_when_the_predicate_does_not_hold():
    derived = weakened_when(Const(False), Confidence.HEURISTIC).evaluate(EvalContext())
    assert derived.value is True
    assert derived.confidence is Confidence.DECLARED
    assert derived.detail["applied"] is False


def test_weaken_meets_the_predicates_own_evidence_in():
    applied = weakened_when(Const(True, Confidence.INFERRED), Confidence.HEURISTIC)
    assert applied.evaluate(EvalContext()).confidence is Confidence.HEURISTIC
    unapplied = weakened_when(Const(False, Confidence.INFERRED), Confidence.HEURISTIC)
    assert unapplied.evaluate(EvalContext()).confidence is Confidence.INFERRED


def test_weaken_never_drops_a_row(corpus):
    everything = [match.anchor.id for match in symbols().rows(corpus)]
    weakened = symbols().where(weakened_when(Const(True), Confidence.HEURISTIC))
    assert [match.anchor.id for match in weakened.rows(corpus)] == everything
    assert all(match.confidence is Confidence.HEURISTIC for match in weakened.rows(corpus))


def test_weaken_is_order_independent_inside_a_conjunction():
    weaken = weakened_when(Const(True), Confidence.HEURISTIC)
    other = Const(True, Confidence.INFERRED).is_true()
    first = all_of(weaken, other).evaluate(EvalContext())
    last = all_of(other, weaken).evaluate(EvalContext())
    assert first.confidence is last.confidence is Confidence.HEURISTIC


def test_weaken_applies_per_row_from_a_semi_join(corpus):
    # Rows whose module is the one that re-exports through the barrel come back
    # HEURISTIC; every other row stays DECLARED. Nothing is filtered out.
    dynamic = projected_set(
        "dynamic-modules",
        symbols().where(row.file_path.matches("*__init__.py")).project("module"),
    )
    weakened = symbols().where(
        weakened_when(in_set(row.module, dynamic), Confidence.HEURISTIC)
    )
    by_id = {match.anchor.id: match.confidence for match in weakened.rows(corpus)}
    assert by_id["pkg:Thing"] is Confidence.HEURISTIC
    assert by_id["app:use"] is Confidence.DECLARED
    assert len(by_id) == len(symbols().rows(corpus))


# ---------------------------------------------------------------------------
# expression-valued right-hand sides
# ---------------------------------------------------------------------------


def test_an_expression_rhs_is_a_child_so_reach_is_derived_through_it():
    assert row.name.eq(column_of(DEFINITION_ID)).reach is Reach.PROJECT
    assert column_of(DEFINITION_MODULE).is_within(row.module).reach is Reach.PROJECT
    assert row.name.eq("Thing").reach is Reach.FILE


def test_an_expression_rhs_contributes_its_field_and_trait_names():
    assert row.name.eq(row.module).field_names == frozenset({"name", "module"})
    compared = row.annotation.eq(trait_of(TYPE_ANNOTATION).value)
    assert compared.trait_names == frozenset({TYPE_ANNOTATION})


def test_a_trait_on_the_right_hand_side_resolves_inside_a_selection(corpus):
    # If rhs traits never reached the lazy trait map this would raise
    # UnknownTraitError instead of answering.
    matched = symbols().where(row.annotation.eq(trait_of(TYPE_ANNOTATION).value))
    assert [match.anchor.id for match in matched.rows(corpus)] != []


def test_an_expression_rhs_field_read_is_validated_against_visible_fields():
    with pytest.raises(UnknownFieldError) as excinfo:
        symbols().project("name").where(row.name.eq(row.module))
    assert excinfo.value.field == "module"


def test_an_expression_rhs_meets_both_sides_evidence():
    node = Const("x", Confidence.DECLARED).eq(Const("x", Confidence.HEURISTIC))
    derived = node.evaluate(EvalContext())
    assert derived.value is True
    assert derived.confidence is Confidence.HEURISTIC
    assert len(derived.inputs) == 2


def test_an_expression_rhs_detail_stays_json_shaped():
    derived = row.name.eq(row.module).evaluate(
        EvalContext(fields={"name": "pkg", "module": "pkg"})
    )
    assert derived.detail["rhs"] == {"expr": "FieldRead"}


def test_an_unmatched_right_hand_side_compares_false():
    node = Const("x").eq(trait_of(TYPE_ANNOTATION).at(Confidence.DECLARED))
    derived = node.evaluate(
        EvalContext(traits={TYPE_ANNOTATION: _trait(None, Confidence.UNKNOWN)})
    )
    assert derived.value is False


def test_is_in_refuses_an_expression_right_hand_side():
    with pytest.raises(ValueError) as excinfo:
        row.kind.is_in(row.name)
    assert "takes a fixed tuple of candidate values" in str(excinfo.value)


def _trait(value, confidence):
    """Build a Trait for direct EvalContext construction."""
    from pypeeker.analysis import Trait

    return Trait(value=value, confidence=confidence, provenance="test")


# ---------------------------------------------------------------------------
# the dotted-name and set-valued comparison truth tables
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "prefix", "expected"),
    [
        ("pkg", "pkg", True),
        ("pkg.mod", "pkg", True),
        ("pkg.sub.mod", "pkg", True),
        ("pkgx", "pkg", False),
        ("pkgx.mod", "pkg", False),
        ("other", "pkg", False),
        (None, "pkg", False),
    ],
)
def test_is_within_truth_table(value, prefix, expected):
    derived = row.module.is_within(prefix).evaluate(EvalContext(fields={"module": value}))
    assert derived.value is expected


@pytest.mark.parametrize(
    ("value", "other", "expected"),
    [
        (frozenset({"app"}), "pkg", True),
        (frozenset({"pkg"}), "pkg", False),
        (frozenset({"pkg", "app"}), "pkg", True),
        (frozenset(), "pkg", False),
        ((), "pkg", False),
        (None, "pkg", False),
        ("pkg", "pkg", False),
    ],
)
def test_any_other_than_truth_table(value, other, expected):
    derived = row.origins.any_other_than(other).evaluate(EvalContext(fields={"origins": value}))
    assert derived.value is expected


@pytest.mark.parametrize(
    ("value", "prefix", "expected"),
    [
        (frozenset({"pkg.mod"}), "pkg", False),
        (frozenset({"pkg"}), "pkg", False),
        (frozenset({"pkgx"}), "pkg", True),
        (frozenset({"pkg.mod", "app"}), "pkg", True),
        (frozenset(), "pkg", False),
        (None, "pkg", False),
    ],
)
def test_any_outside_truth_table(value, prefix, expected):
    derived = row.origins.any_outside(prefix).evaluate(EvalContext(fields={"origins": value}))
    assert derived.value is expected


def test_any_other_than_does_not_quantify_over_a_strings_characters():
    derived = row.origins.any_other_than("p").evaluate(EvalContext(fields={"origins": "pkg"}))
    assert derived.value is False


# ---------------------------------------------------------------------------
# the symbols universe gained imported_from
# ---------------------------------------------------------------------------


def test_symbols_rows_expose_imported_from(corpus):
    assert "imported_from" in universe_fields("symbols")
    (reexport,) = [m for m in symbols().rows(corpus) if m.anchor.id == "pkg:Thing"]
    assert reexport.fields["imported_from"] == "pkg.mod.Thing"
    (declared,) = [m for m in symbols().rows(corpus) if m.anchor.id == "pkg.mod:Thing"]
    assert declared.fields["imported_from"] is None


def test_imported_from_is_declared_evidence_unlike_the_imports_universe(corpus):
    reexports = symbols().where(
        all_of(row.kind.eq(SymbolKind.IMPORT), row.imported_from.is_true())
    )
    assert all(match.confidence is Confidence.DECLARED for match in reexports.rows(corpus))
