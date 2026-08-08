"""``with_field``: projection's dual, and the rules that keep it from rewriting the model.

A rule's wording is a ``str.format`` template over the row's visible fields
(fork #9 — a callable message would hide rule semantics from the derivation
tree). A derived field is how a template can say something the model does not
carry as a field: which packages a boundary violation is between, which modules
share a cycle, and — for the modules universe, which publishes no ``line`` — at
which line to report at all.

The invariants pinned here are the ones that keep that from becoming a second,
quieter way to define what a row means: a derived field may only add, its
evidence is not free, and it is invisible to everything written before it.
"""

import pytest

from pypeeker.dsl import (
    Const,
    Corpus,
    DerivedFieldError,
    Fact,
    OpaquePredicateError,
    Reach,
    UnknownFieldError,
    all_of,
    fact_of,
    mapping_table,
    modules,
    row,
    symbols,
)
from pypeeker.analysis import Trait
from pypeeker.models import Confidence

LIB = """\
def helper():
    return 1
"""


@pytest.fixture
def corpus(indexed_project):
    """A one-file corpus; these tests are about stage mechanics, not row supply."""
    _, store = indexed_project({"lib.py": LIB})
    return Corpus(store)


def _verdict_fact(value, confidence, *, reach=Reach.PROJECT, name="verdict"):
    """A fact answering the same trait for every anchor, at a stated confidence."""
    return Fact(
        name=name,
        reach=reach,
        compute=lambda corpus, params: mapping_table({
            "lib": Trait(value, confidence, "test sweep"),
        }),
    )


# ---------------------------------------------------------------------------
# it adds a field, and the field is real
# ---------------------------------------------------------------------------


def test_a_derived_field_is_visible_on_the_returned_rows(corpus):
    matches = modules().with_field("line", Const(1)).rows(corpus)
    assert [match.fields["line"] for match in matches] == [1]
    assert matches[0].fields["module"] == "lib"


def test_a_derived_field_is_readable_by_a_later_where(corpus):
    selection = (
        modules().with_field("line", Const(1)).where(row.line.eq(1))
    )
    assert [match.fields["module"] for match in selection.rows(corpus)] == ["lib"]


def test_a_derived_field_can_be_projected_and_survives(corpus):
    selection = modules().with_field("line", Const(7)).project("module", "line")
    matches = selection.rows(corpus)
    assert dict(matches[0].fields) == {"module": "lib", "line": 7}


def test_a_derived_field_appears_in_the_written_program(corpus):
    del corpus
    selection = modules().with_field("line", Const(1)).where(row.line.eq(1))
    assert selection.stages == ("with_field:line", "where")


def test_a_derived_field_widens_the_declared_field_set(corpus):
    del corpus
    assert "line" not in modules().fields
    assert "line" in modules().with_field("line", Const(1)).fields


# ---------------------------------------------------------------------------
# it computes, it does not filter
# ---------------------------------------------------------------------------


def test_a_falsy_derived_value_adds_a_falsy_field_rather_than_dropping_the_row(corpus):
    matches = modules().with_field("flag", Const(False)).rows(corpus)
    assert len(matches) == 1
    assert matches[0].fields["flag"] is False


def test_a_derived_field_over_a_missing_fact_reads_as_none(corpus):
    absent = Fact(
        name="absent",
        reach=Reach.PROJECT,
        compute=lambda c, p: mapping_table({}),
    )
    matches = modules().with_field("verdict", fact_of(absent).value).rows(corpus)
    assert matches[0].fields["verdict"] is None


# ---------------------------------------------------------------------------
# its evidence is not free
# ---------------------------------------------------------------------------


def test_a_derived_fields_confidence_meets_into_the_row(corpus):
    """A message quoting a HEURISTIC sweep cannot be reported as DECLARED."""
    spec = _verdict_fact("shaky", Confidence.HEURISTIC)
    matches = modules().with_field("verdict", fact_of(spec).value).rows(corpus)
    assert matches[0].fields["verdict"] == "shaky"
    assert matches[0].confidence is Confidence.HEURISTIC


def test_a_declared_derived_field_leaves_the_row_declared(corpus):
    matches = modules().with_field("line", Const(1)).rows(corpus)
    assert matches[0].confidence is Confidence.DECLARED


def test_the_meta_read_law_applies_through_a_derived_field(corpus):
    """Reading a sweep's level is DECLARED even when the sweep itself is not."""
    spec = _verdict_fact("shaky", Confidence.HEURISTIC)
    matches = modules().with_field("tier", fact_of(spec).confidence).rows(corpus)
    assert matches[0].fields["tier"] is Confidence.HEURISTIC
    assert matches[0].confidence is Confidence.DECLARED


def test_the_derivation_is_appended_in_written_order(corpus):
    spec = _verdict_fact("shaky", Confidence.HEURISTIC)
    selection = (
        modules().where(row.module.eq("lib")).with_field("verdict", fact_of(spec).value)
    )
    ops = [node.op for node in selection.rows(corpus)[0].derivations]
    assert ops == ["compare.eq", "fact.value"]


# ---------------------------------------------------------------------------
# reach: the blocking correctness property
# ---------------------------------------------------------------------------


def test_a_project_reach_derived_field_makes_the_selection_project(corpus):
    """A stage that consults the resolver must say so, whatever kind of stage it is."""
    del corpus
    spec = _verdict_fact("x", Confidence.DECLARED)
    selection = modules().with_field("verdict", fact_of(spec).value)
    assert selection.reach is Reach.PROJECT


def test_a_file_reach_derived_field_leaves_the_selection_at_file(corpus):
    del corpus
    assert modules().with_field("line", Const(1)).reach is Reach.FILE


def test_reach_is_project_even_when_only_the_derived_field_leaves_the_file(corpus):
    del corpus
    spec = _verdict_fact("x", Confidence.DECLARED)
    selection = (
        modules().where(row.module.eq("lib")).with_field("verdict", fact_of(spec).value)
    )
    assert selection.reach is Reach.PROJECT


# ---------------------------------------------------------------------------
# what it refuses
# ---------------------------------------------------------------------------


def test_shadowing_a_model_field_is_refused_by_name():
    with pytest.raises(DerivedFieldError) as excinfo:
        modules().with_field("module", Const("other"))
    assert excinfo.value.field == "module"
    assert excinfo.value.code == "derived-field"
    assert "would shadow a field of the modules universe" in str(excinfo.value)
    assert "file_path" in str(excinfo.value)


def test_shadowing_an_earlier_derived_field_is_refused_too():
    with pytest.raises(DerivedFieldError):
        modules().with_field("line", Const(1)).with_field("line", Const(2))


def test_a_derived_field_may_reuse_a_name_an_earlier_project_dropped():
    """project() really removed it, so redefining it invents nothing."""
    selection = (
        modules().project("module").with_field("file_path", Const("pyproject.toml"))
    )
    assert selection.fields == frozenset({"module", "file_path"})


def test_a_derived_field_reading_a_field_that_is_not_visible_is_refused():
    with pytest.raises(UnknownFieldError) as excinfo:
        modules().with_field("copy", row.symbol_id)
    assert excinfo.value.field == "symbol_id"
    assert "modules universe" in str(excinfo.value)


def test_a_derived_field_reading_a_projected_away_field_is_refused():
    with pytest.raises(UnknownFieldError) as excinfo:
        modules().project("module").with_field("copy", row.file_path)
    assert excinfo.value.field == "file_path"


def test_a_bare_callable_is_refused_as_a_derived_field(corpus):
    del corpus

    def compute(record):
        return 1

    with pytest.raises(OpaquePredicateError) as excinfo:
        modules().with_field("line", compute)
    assert "with_field() takes an expression" in str(excinfo.value)
    assert "'compute'" in str(excinfo.value)


def test_a_non_expression_is_a_type_error():
    with pytest.raises(TypeError) as excinfo:
        modules().with_field("line", 1)
    assert "with_field() takes an expression, got int" in str(excinfo.value)


# ---------------------------------------------------------------------------
# written order stays normative
# ---------------------------------------------------------------------------


def test_a_derived_field_is_invisible_to_an_earlier_stage():
    with pytest.raises(UnknownFieldError) as excinfo:
        modules().where(row.line.eq(1)).with_field("line", Const(1))
    assert excinfo.value.field == "line"


def test_adding_a_derived_field_does_not_mutate_the_selection_it_came_from(corpus):
    base = modules()
    widened = base.with_field("line", Const(1))
    assert "line" not in base.fields
    assert "line" in widened.fields
    assert "line" not in dict(base.rows(corpus)[0].fields)


def test_a_derived_field_does_not_leak_into_rows_of_a_sibling_selection(corpus):
    """_Record is frozen and shares its fields mapping between copies; rebuild, never write."""
    base = symbols().where(row.name.eq("helper"))
    widened = base.with_field("verdict", Const("computed"))
    assert [match.fields["verdict"] for match in widened.rows(corpus)] == ["computed"]
    assert "verdict" not in dict(base.rows(corpus)[0].fields)


def test_two_derived_fields_compose_in_written_order(corpus):
    selection = (
        modules()
        .with_field("line", Const(1))
        .with_field("label", Const("x"))
        .where(all_of(row.line.eq(1), row.label.eq("x")))
    )
    assert len(selection.rows(corpus)) == 1
    assert selection.stages == ("with_field:line", "with_field:label", "where")
