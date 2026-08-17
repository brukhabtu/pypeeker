"""The primitive fact tier: what a sweep is worth, when it runs, and what it may not leak.

Fork #11 puts fixpoint- and graph-shaped computations in hand-written Python
declaring their own confidence. These tests pin the mechanism that carries
them: that a fact's declared reach really does drive a selection's reach, that
a sweep runs once per corpus rather than once per row, that a fact read outside
a selection is a loud refusal rather than a silent false, and that the
``Trait`` a sweep returns never gets its provenance prose into a derivation.
"""

import pytest

from pypeeker.analysis import Trait
from pypeeker.dsl import (
    Corpus,
    Fact,
    FactRead,
    FactRow,
    ReachError,
    Reach,
    UnknownFactError,
    UnknownTraitError,
    Selection,
    all_of,
    derivation_to_dict,
    fact_of,
    fact_source,
    fact_specs,
    lazy_table,
    mapping_table,
    modules,
    row,
    trait,
    trait_of,
)
from pypeeker.dsl.anchors import AnchorKind
from pypeeker.models import Confidence

LIB = """\
def helper():
    return 1
"""

APP = """\
def go():
    return 2
"""


@pytest.fixture
def corpus(indexed_project):
    """A two-file corpus, so a project-wide sweep has more than one index to see."""
    _, store = indexed_project({"lib.py": LIB, "app.py": APP})
    return Corpus(store)


def _module_verdict_fact(calls, *, reach=Reach.PROJECT, name="module-verdict"):
    """A fact whose sweep records every call, so a test can count computations."""

    def compute(corpus, params):
        calls.append(params)
        return mapping_table({
            index.file_path.removesuffix(".py"): Trait(
                value=f"{params}:{index.file_path}",
                confidence=Confidence.HEURISTIC,
                provenance="test sweep: do not leak me",
            )
            for index in corpus.indexes
        })

    return Fact(name=name, reach=reach, compute=compute)


# ---------------------------------------------------------------------------
# what a fact read is worth
# ---------------------------------------------------------------------------


def test_a_fact_value_carries_the_confidence_the_sweep_declared(corpus):
    """Fork #11: the primitive declares its own confidence, and the row inherits it."""
    spec = _module_verdict_fact([])
    matches = modules().where(fact_of(spec, "v1").value.is_true()).rows(corpus)
    assert {match.fields["module"] for match in matches} == {"app", "lib"}
    assert {match.confidence for match in matches} == {Confidence.HEURISTIC}


def test_reading_a_facts_confidence_is_a_declared_meta_read(corpus):
    """Fork #4's meta-read law, applied at the leaf that does the reading."""
    spec = _module_verdict_fact([])
    matches = (
        modules()
        .where(fact_of(spec, "v1").confidence.eq(Confidence.HEURISTIC))
        .rows(corpus)
    )
    assert len(matches) == 2
    assert {match.confidence for match in matches} == {Confidence.DECLARED}


def test_an_anchor_the_sweep_has_nothing_for_reads_as_none_at_unknown():
    """Evaluation stays total: a missing entry is an answer, not an exception."""
    spec = Fact(
        name="sparse",
        reach=Reach.FILE,
        compute=lambda corpus, params: mapping_table({}),
    )
    node = FactRead(spec, None).evaluate(
        _context_over({"sparse": None})
    )
    assert node.value is None
    assert node.confidence is Confidence.UNKNOWN
    assert node.detail["present"] is False


def test_a_missing_fact_is_unknown_under_the_confidence_projection_too():
    """A sweep that recorded no level has nothing declared to report about one."""
    spec = Fact(name="sparse", reach=Reach.FILE, compute=lambda c, p: mapping_table({}))
    node = FactRead(spec, None, "confidence").evaluate(_context_over({"sparse": None}))
    assert node.value is None
    assert node.confidence is Confidence.UNKNOWN


def _context_over(answers):
    """An EvalContext whose fact resolver answers from ``answers``."""
    from pypeeker.dsl import EvalContext

    return EvalContext(facts=lambda name, params: answers[name])


# ---------------------------------------------------------------------------
# no provenance prose escapes into a derivation
# ---------------------------------------------------------------------------


def test_a_facts_provenance_prose_never_reaches_the_derivation_tree(corpus):
    """The same discipline TraitRead holds to: provenance is debugging prose, not output."""
    spec = _module_verdict_fact([])
    matches = modules().where(fact_of(spec, "v1").value.is_true()).rows(corpus)
    rendered = repr(derivation_to_dict(matches[0].derivations[0]))
    assert "do not leak me" not in rendered
    assert "fact.value" in rendered
    assert "module-verdict" in rendered


def test_a_fact_read_declares_itself_in_the_derivations_reads(corpus):
    """--why must name the sweep a finding rests on, prefixed when it left the file."""
    spec = _module_verdict_fact([])
    matches = modules().where(fact_of(spec, "v1").value.is_true()).rows(corpus)
    assert matches[0].derivations[0].reads == frozenset({"project:fact:module-verdict"})


def test_a_file_reach_fact_read_carries_no_project_prefix(corpus):
    """The token is honest about the reach the spec declared, not uniformly pessimistic."""
    spec = _module_verdict_fact([], reach=Reach.FILE, name="local-verdict")
    matches = modules().where(fact_of(spec).value.is_true()).rows(corpus)
    assert matches[0].derivations[0].reads == frozenset({"fact:local-verdict"})


# ---------------------------------------------------------------------------
# reach is declared next to the sweep, and really does propagate
# ---------------------------------------------------------------------------


def test_a_project_reach_fact_forces_the_whole_selection_to_project():
    """The one input to Selection.reach for a fact read is the spec's own declaration."""
    spec = _module_verdict_fact([])
    assert modules().where(fact_of(spec).value.is_true()).reach is Reach.PROJECT


def test_a_file_reach_fact_leaves_the_selection_at_file():
    spec = _module_verdict_fact([], reach=Reach.FILE, name="local-verdict")
    assert modules().where(fact_of(spec).value.is_true()).reach is Reach.FILE


def test_naming_a_project_reach_fact_expression_as_a_trait_is_refused():
    """architecture.md's trait-promotion clause (b): no store, no resolver, no substrate."""
    spec = _module_verdict_fact([])
    with pytest.raises(ReachError) as excinfo:
        trait("borrowed-sweep", fact_of(spec).value.is_true())
    assert "primitive trait" in str(excinfo.value)


# ---------------------------------------------------------------------------
# the sweep runs once per corpus, keyed on name AND params
# ---------------------------------------------------------------------------


def test_a_sweep_runs_once_however_many_rows_and_stages_read_it(corpus):
    """The corpus is the lifetime of a project-wide sweep, not the row."""
    calls = []
    spec = _module_verdict_fact(calls)
    selection = (
        modules()
        .where(fact_of(spec, "v1").value.is_true())
        .with_field("verdict", fact_of(spec, "v1").value)
    )
    matches = selection.rows(corpus)
    assert len(matches) == 2
    assert calls == ["v1"]


def test_different_params_are_different_computations(corpus):
    """Params are part of the memo key, so two configurations cannot share an answer."""
    calls = []
    spec = _module_verdict_fact(calls)
    modules().where(
        all_of(fact_of(spec, "v1").value.is_true(), fact_of(spec, "v2").value.is_true())
    ).rows(corpus)
    assert calls == ["v1", "v2"]


def test_the_memo_survives_across_selections_over_one_corpus(corpus):
    calls = []
    spec = _module_verdict_fact(calls)
    modules().where(fact_of(spec, "v1").value.is_true()).rows(corpus)
    modules().where(fact_of(spec, "v1").value.is_true()).rows(corpus)
    assert calls == ["v1"]


def test_a_lazy_table_computes_each_anchor_once_and_only_when_asked(corpus):
    """Purity's shape: the expensive walk stays behind the cheap clauses, and never repeats."""
    asked = []

    def compute(corpus_, params):
        del corpus_, params

        def per_anchor(anchor_id):
            asked.append(anchor_id)
            return Trait(anchor_id, Confidence.DECLARED, "test")

        return lazy_table(per_anchor)

    spec = Fact(name="expensive", reach=Reach.PROJECT, compute=compute)
    selection = (
        modules()
        # `module` is "app" or "lib"; only one row gets past this clause, so
        # only one anchor may ever reach the sweep.
        .where(all_of(row.module.eq("lib"), fact_of(spec).value.is_true()))
        .with_field("verdict", fact_of(spec).value)
    )
    matches = selection.rows(corpus)
    assert [match.fields["verdict"] for match in matches] == ["lib"]
    # once for the where clause that accepted it; the with_field read is served
    # from the table's own cache, not recomputed.
    assert asked == ["lib"]


# ---------------------------------------------------------------------------
# a fact read with no corpus behind it is a loud refusal
# ---------------------------------------------------------------------------


def test_a_fact_read_outside_a_selection_refuses_by_name():
    from pypeeker.dsl import EvalContext

    spec = _module_verdict_fact([])
    with pytest.raises(UnknownFactError) as excinfo:
        FactRead(spec, "v1").evaluate(EvalContext())
    assert excinfo.value.fact == "module-verdict"
    assert excinfo.value.code == "unknown-fact"
    assert "no fact table can answer 'module-verdict'" in str(excinfo.value)
    assert "(none)" in str(excinfo.value)


def test_the_refusal_survives_a_comparison_and_a_negation():
    """consuming_falsity() must carry the resolver, or every nested read would refuse."""
    from pypeeker.dsl import EvalContext, not_

    spec = _module_verdict_fact([])
    answered = []

    def resolver(name, params):
        answered.append((name, params))
        return Trait(value=False, confidence=Confidence.INFERRED, provenance="test")

    context = EvalContext(discards_falsity=True, facts=resolver)
    node = not_(fact_of(spec, "v1").value.is_true()).evaluate(context)
    assert node.value is True
    assert node.confidence is Confidence.INFERRED
    assert answered == [("module-verdict", "v1")]


def test_params_must_be_hashable_because_expression_nodes_are_shared():
    spec = _module_verdict_fact([])
    with pytest.raises(TypeError) as excinfo:
        fact_of(spec, {"allow": ["x"]}).value
    assert "must be hashable" in str(excinfo.value)


# ---------------------------------------------------------------------------
# .about(): reading a fact about the entity a row NAMES
# ---------------------------------------------------------------------------


def _keyed_fact(entries, *, name="keyed", reach=Reach.PROJECT):
    """A fact whose table is spelled out: ``{anchor id: (value, confidence)}``."""

    def compute(corpus_, params):
        del corpus_, params
        return mapping_table({
            key: Trait(value=value, confidence=level, provenance="test sweep")
            for key, (value, level) in entries.items()
        })

    return Fact(name=name, reach=reach, compute=compute)


def test_about_reads_the_entry_for_the_entity_the_row_names(corpus):
    """The row stays the finding's subject; the fact is read where it is defined."""
    spec = _keyed_fact({
        "never": ("by-name", Confidence.DECLARED),
        "alpha->never": ("by-row", Confidence.DECLARED),
    })
    source = _allowance_source([("alpha", "never", False)])
    matches = (
        Selection(source)
        .with_field("named", fact_of(spec).about(row.dep_pkg).value)
        .with_field("own", fact_of(spec).value)
        .rows(corpus)
    )
    assert matches[0].fields["named"] == "by-name"
    assert matches[0].fields["own"] == "by-row"
    # and the finding is still anchored at the row, not at what it asked about
    assert matches[0].anchor.id == "alpha->never"


def test_an_anchor_that_is_not_an_entity_id_reads_as_none_at_unknown():
    """Total evaluation: an anchor naming nothing is the same answer as a missing entry."""
    from pypeeker.dsl import UNMATCHED, EvalContext

    spec = _keyed_fact({"never": ("stale", Confidence.DECLARED)})
    asked = []

    def resolver(name, params, anchor=None):
        asked.append(anchor)
        return Trait("must not be read", Confidence.DECLARED, "test")

    for absent in (None, UNMATCHED):
        node = FactRead(spec, None, "value", row.dep_pkg).evaluate(
            EvalContext(fields={"dep_pkg": absent}, facts=resolver)
        )
        assert node.value is None
        assert node.confidence is Confidence.UNKNOWN
        assert node.detail["present"] is False
        assert node.detail["anchor"] is None
    # the table is never consulted for such a row, so a lazy sweep pays nothing
    assert asked == []


def test_the_anchors_confidence_meets_into_the_read(corpus):
    """A guessed identification of WHICH entity cannot yield a DECLARED answer about it."""
    names = _keyed_fact(
        {"alpha->never": ("never", Confidence.HEURISTIC)}, name="names"
    )
    verdicts = _keyed_fact(
        {"never": ("stale", Confidence.DECLARED)}, name="verdicts"
    )
    source = _allowance_source([("alpha", "never", False)])
    matches = (
        Selection(source)
        .with_field("verdict", fact_of(verdicts).about(fact_of(names).value).value)
        .rows(corpus)
    )
    assert matches[0].fields["verdict"] == "stale"
    assert matches[0].confidence is Confidence.HEURISTIC


def test_the_anchors_reads_join_the_reads_the_derivation_declares(corpus):
    """--why must name every sweep a finding rests on, the anchor's included."""
    names = _keyed_fact(
        {"alpha->never": ("never", Confidence.DECLARED)}, name="names"
    )
    verdicts = _keyed_fact(
        {"never": ("stale", Confidence.DECLARED)}, name="verdicts"
    )
    source = _allowance_source([("alpha", "never", False)])
    matches = (
        Selection(source)
        .where(fact_of(verdicts).about(fact_of(names).value).value.is_true())
        .rows(corpus)
    )
    node = matches[0].derivations[0]
    assert node.reads == frozenset({"project:fact:verdicts", "project:fact:names"})
    read = node.inputs[0]
    assert read.op == "fact.value"
    assert read.detail["anchor"] == "never"
    # the anchor's own derivation hangs under the read, so --why shows how the
    # entity that was asked about was arrived at
    assert read.inputs[0].detail["fact"] == "names"


def test_a_field_read_inside_an_anchor_is_validated_like_any_other(corpus):
    """The anchor is a real child node, so project() narrowing reaches into it."""
    from pypeeker.dsl import UnknownFieldError

    spec = _keyed_fact({"never": ("stale", Confidence.DECLARED)})
    source = _allowance_source([("alpha", "never", False)])
    with pytest.raises(UnknownFieldError) as excinfo:
        (
            Selection(source)
            .project("importer_pkg")
            .where(fact_of(spec).about(row.dep_pkg).value.is_true())
        )
    assert excinfo.value.field == "dep_pkg"


def test_a_fact_reads_reach_joins_its_anchors():
    """Reach is derived from the whole node, spec and anchor alike."""
    from pypeeker.dsl import opaque

    spec = _keyed_fact({}, name="local", reach=Reach.FILE)

    @opaque("names-elsewhere", reads=("project:definition",))
    def _elsewhere(record):
        del record
        return "never"

    assert fact_of(spec).value.reach is Reach.FILE
    assert fact_of(spec).about(_elsewhere).value.reach is Reach.PROJECT


def test_fact_specs_walks_through_an_anchor_expression():
    """A sweep named only inside an anchor still has to be computed for the run."""
    one = _module_verdict_fact([], name="alpha")
    two = _module_verdict_fact([], name="beta")
    expr = fact_of(one).about(fact_of(two).value).value
    assert [spec.name for spec in fact_specs(expr)] == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# spec discovery
# ---------------------------------------------------------------------------


def test_fact_specs_finds_every_spec_in_a_tree_deduplicated_by_name():
    one = _module_verdict_fact([], name="alpha")
    two = _module_verdict_fact([], name="beta")
    expr = all_of(
        fact_of(two, "x").value.is_true(),
        fact_of(one).value.is_true(),
        fact_of(one, "y").confidence.eq(Confidence.DECLARED),
    )
    assert [spec.name for spec in fact_specs(expr)] == ["alpha", "beta"]


def test_fact_specs_is_empty_for_an_expression_reading_no_facts():
    assert fact_specs(row.module.eq("lib")) == ()


# ---------------------------------------------------------------------------
# fact_source: rows over something the model does not hold
# ---------------------------------------------------------------------------


def _allowance_source(pairs, *, name="allowances"):
    """A row source over configuration, the shape report-unused-allowances needs."""

    def rows(corpus):
        del corpus
        for importer, dep, exercised in pairs:
            yield FactRow(
                anchor_id=f"{importer}->{dep}",
                fields={
                    "importer_pkg": importer,
                    "dep_pkg": dep,
                    "exercised": exercised,
                    "file_path": "pyproject.toml",
                    "line": 1,
                },
            )

    return fact_source(
        name,
        ("importer_pkg", "dep_pkg", "exercised", "file_path", "line"),
        rows,
        anchor_kind=AnchorKind.MODULE,
    )


def test_a_fact_source_yields_rows_a_selection_can_filter(corpus):
    source = _allowance_source([("alpha", "beta", True), ("alpha", "never", False)])
    matches = Selection(source).where(row.exercised.eq(False)).rows(corpus)
    assert [match.fields["dep_pkg"] for match in matches] == ["never"]
    assert matches[0].fields["file_path"] == "pyproject.toml"
    assert matches[0].fields["line"] == 1
    assert matches[0].anchor.id == "alpha->never"
    assert matches[0].confidence is Confidence.DECLARED


def test_a_fact_source_row_is_not_in_a_file_so_it_offers_no_traits(corpus):
    """No FileIndex, so a trait read is refused by name rather than crashing."""
    source = _allowance_source([("alpha", "never", False)])
    with pytest.raises(UnknownTraitError) as excinfo:
        Selection(source).where(trait_of("type-annotation").value.eq("list")).rows(corpus)
    assert excinfo.value.trait == "type-annotation"


def test_a_fact_source_declares_its_fields_and_refuses_the_rest(corpus):
    from pypeeker.dsl import UnknownFieldError

    source = _allowance_source([("alpha", "never", False)])
    with pytest.raises(UnknownFieldError) as excinfo:
        Selection(source).where(row.symbol_id.eq("x"))
    assert excinfo.value.field == "symbol_id"
    assert "importer_pkg" in str(excinfo.value)


def test_a_fact_source_never_becomes_a_sixth_named_universe():
    """Fork #8 fixes the model universes at five; a fact source is not one of them."""
    from pypeeker.dsl import UNIVERSE_NAMES, UnknownUniverseError, universe_fields

    _allowance_source([("alpha", "never", False)])
    assert UNIVERSE_NAMES == ("symbols", "references", "imports", "modules", "scopes")
    with pytest.raises(UnknownUniverseError):
        universe_fields("allowances")


def test_a_fact_source_declares_no_follow_steps(corpus):
    from pypeeker.dsl import UnknownFollowError

    source = _allowance_source([("alpha", "never", False)])
    with pytest.raises(UnknownFollowError) as excinfo:
        Selection(source).follow("module")
    assert excinfo.value.step == "module"


def test_a_row_source_must_declare_exactly_one_shape():
    """A universe with both shapes, or neither, is a construction error not a runtime one."""
    from pypeeker.dsl.universes import _Universe

    with pytest.raises(ValueError) as excinfo:
        _Universe(name="broken", anchor_kind=AnchorKind.MODULE, fields=())
    assert "exactly one row source" in str(excinfo.value)
