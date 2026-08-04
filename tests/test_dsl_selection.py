"""Selection semantics: the five universes, written order, derived reach, loud authoring errors.

Every assertion here is about behaviour a caller can observe — which rows come
back, in what order, on what confidence, and the exact wording of a refusal —
rather than about the existence of a name.
"""

import pytest

from pypeeker.analysis import TYPE_ANNOTATION
from pypeeker.dsl import (
    Corpus,
    OpaquePredicateError,
    Reach,
    Selection,
    UNIVERSE_NAMES,
    UnknownFieldError,
    UnknownFollowError,
    UnknownTraitError,
    UnknownUniverseError,
    all_of,
    imports,
    modules,
    not_,
    opaque,
    references,
    row,
    scopes,
    symbols,
    trait_of,
    universe_fields,
    universe_follows,
)
from pypeeker.models import Confidence, ScopeKind, SymbolKind, Visibility

LIB = """\
def helper():
    return 1
"""

APP = """\
import importlib
from lib import helper


def go():
    items = [1, 2]
    return helper() + len(items)


class Widget:
    def paint(self):
        return None


_private = importlib.import_module("json")
"""


@pytest.fixture
def corpus(indexed_project):
    """A two-file corpus: ``lib.py`` and ``app.py``, both under no src root filter."""
    _, store = indexed_project({"lib.py": LIB, "app.py": APP})
    return Corpus(store)


# ---------------------------------------------------------------------------
# the five universes
# ---------------------------------------------------------------------------


def test_universe_names_are_the_five_forks_fix():
    assert UNIVERSE_NAMES == ("symbols", "references", "imports", "modules", "scopes")


def test_symbols_universe_yields_every_declared_symbol(corpus):
    ids = [match.anchor.id for match in symbols().rows(corpus)]
    assert "app:go" in ids
    assert "app:go:items" in ids
    assert "app:Widget.paint" in ids
    assert "lib:helper" in ids
    # the file's own MODULE symbol is a symbol like any other
    assert "app" in ids and "lib" in ids


def test_symbol_row_fields_are_the_declared_table(corpus):
    (match,) = [m for m in symbols().rows(corpus) if m.anchor.id == "app:go:items"]
    assert set(match.fields) == set(universe_fields("symbols"))
    assert match.fields["name"] == "items"
    assert match.fields["kind"] is SymbolKind.VARIABLE
    assert match.fields["scope_kind"] == ScopeKind.FUNCTION.value
    assert match.fields["module"] == "app"
    assert match.fields["annotation"] == "list"
    assert match.fields["is_module_level"] is False


def test_symbol_line_is_one_based(corpus):
    (match,) = [m for m in symbols().rows(corpus) if m.anchor.id == "app:go"]
    # `def go():` is the fifth line of APP.
    assert match.fields["line"] == 5


def test_module_level_flag_distinguishes_nesting(corpus):
    module_level = {
        m.anchor.id: m.fields["is_module_level"]
        for m in symbols().rows(corpus)
        if m.anchor.id in {"app:go", "app:go:items"}
    }
    assert module_level == {"app:go": True, "app:go:items": False}


def test_modules_universe_yields_one_row_per_indexed_file(corpus):
    rows = modules().rows(corpus)
    assert [m.fields["module"] for m in rows] == ["app", "lib"]
    assert [m.fields["file_path"] for m in rows] == ["app.py", "lib.py"]
    assert all(m.fields["has_errors"] is False for m in rows)


def test_scopes_universe_reports_one_based_spans(corpus):
    (paint,) = [m for m in scopes().rows(corpus) if m.anchor.id == "app:Widget.paint"]
    assert paint.fields["kind"] is ScopeKind.FUNCTION
    assert paint.fields["start_line"] == 11
    assert paint.fields["module"] == "app"


def test_references_universe_anchors_on_the_use_site(corpus):
    reads = [
        m for m in references().rows(corpus) if m.fields["symbol_id"] == "app:go:items"
    ]
    assert reads, "expected at least one reference to app:go:items"
    anchor = reads[0].anchor.id
    assert anchor.startswith("app:go:items@app.py:")
    # `len(items)` sits on the seventh line of APP.
    assert anchor == "app:go:items@app.py:7:26"


def test_imports_universe_yields_only_import_bindings(corpus):
    names = sorted(m.fields["name"] for m in imports().rows(corpus))
    assert names == ["<dynamic-import>", "helper", "importlib"]


# ---------------------------------------------------------------------------
# row evidence: the door sub-DECLARED confidence comes in through
# ---------------------------------------------------------------------------


def test_static_import_rows_carry_declared_evidence(corpus):
    (static,) = [m for m in imports().rows(corpus) if m.fields["name"] == "helper"]
    assert static.confidence is Confidence.DECLARED
    assert static.anchor.evidence is Confidence.DECLARED


def test_dynamically_recovered_import_rows_carry_heuristic_evidence(corpus):
    dynamic = [m for m in imports().rows(corpus) if m.fields["imported_from"] == "json"]
    assert len(dynamic) == 1
    assert dynamic[0].confidence is Confidence.HEURISTIC
    assert dynamic[0].anchor.evidence is Confidence.HEURISTIC
    assert dynamic[0].fields["import_confidence"] is Confidence.HEURISTIC


def test_row_evidence_weakens_a_match_without_the_expression_asking(corpus):
    """No clause here mentions confidence; the row drags the finding down on its own."""
    matched = imports().where(row.imported_from.eq("json")).rows(corpus)
    assert [m.confidence for m in matched] == [Confidence.HEURISTIC]


def test_symbol_rows_are_declared(corpus):
    assert all(m.confidence is Confidence.DECLARED for m in symbols().rows(corpus))


# ---------------------------------------------------------------------------
# written order, with no optimizer
# ---------------------------------------------------------------------------


def _tracing_clauses():
    """Two opaque predicates that record every row they are asked about."""
    seen: list[tuple[str, str]] = []

    @opaque("is-function", reads=("kind",))
    def is_function(candidate):
        seen.append(("kind", candidate.symbol_id))
        return candidate.kind is SymbolKind.FUNCTION

    @opaque("is-public", reads=("visibility",))
    def is_public(candidate):
        seen.append(("visibility", candidate.symbol_id))
        return candidate.visibility is Visibility.PUBLIC

    return seen, is_function, is_public


def test_clauses_run_in_written_order_and_later_clauses_see_fewer_rows(corpus):
    seen, is_function, is_public = _tracing_clauses()
    symbols().where(is_function).where(is_public).rows(corpus)
    kinds = [tag for tag, _ in seen]
    # every "kind" probe happens before any "visibility" probe: stage order, not
    # interleaved row-at-a-time evaluation.
    assert kinds == sorted(kinds, key=lambda tag: 0 if tag == "kind" else 1)
    assert kinds.count("visibility") < kinds.count("kind")


def test_swapping_two_where_clauses_changes_what_each_one_sees(corpus):
    seen_a, is_function_a, is_public_a = _tracing_clauses()
    symbols().where(is_function_a).where(is_public_a).rows(corpus)

    seen_b, is_function_b, is_public_b = _tracing_clauses()
    symbols().where(is_public_b).where(is_function_b).rows(corpus)

    forward = (
        [t for t, _ in seen_a].count("kind"),
        [t for t, _ in seen_a].count("visibility"),
    )
    reversed_ = (
        [t for t, _ in seen_b].count("kind"),
        [t for t, _ in seen_b].count("visibility"),
    )
    # An optimizer normalizing clause order would make these identical.
    assert forward != reversed_


def test_swapping_clauses_does_not_change_the_result_set(corpus):
    _, is_function_a, is_public_a = _tracing_clauses()
    _, is_function_b, is_public_b = _tracing_clauses()
    forward = symbols().where(is_function_a).where(is_public_a).rows(corpus)
    reversed_ = symbols().where(is_public_b).where(is_function_b).rows(corpus)
    assert [m.anchor.id for m in forward] == [m.anchor.id for m in reversed_]
    assert [m.confidence for m in forward] == [m.confidence for m in reversed_]


def test_a_negated_conjunction_reports_one_confidence_whatever_the_clause_order(corpus):
    """Written order schedules the work; it must not decide how well evidenced the answer is.

    ``not_()`` consumes the conjunction's falsity, so both clauses are real
    contributions even for a row the conjunction rejected. Meeting over only
    the clauses a short circuit happened to run would report the cheap
    DECLARED field read alone and claim DECLARED for an answer that also rests
    on an INFERRED trait.
    """
    kind_first = not_(
        all_of(row.kind.eq(SymbolKind.FUNCTION), trait_of(TYPE_ANNOTATION).value.eq("list"))
    )
    trait_first = not_(
        all_of(trait_of(TYPE_ANNOTATION).value.eq("list"), row.kind.eq(SymbolKind.FUNCTION))
    )
    forward = symbols().where(row.name.eq("items")).where(kind_first).rows(corpus)
    reversed_ = symbols().where(row.name.eq("items")).where(trait_first).rows(corpus)

    assert [m.anchor.id for m in forward] == ["app:go:items"]
    assert [m.anchor.id for m in reversed_] == ["app:go:items"]
    assert [m.confidence for m in forward] == [m.confidence for m in reversed_]
    # `items = [1, 2]` is an INFERRED list, and the row was rejected as a
    # function on DECLARED evidence: the meet over both is INFERRED.
    assert forward[0].confidence is Confidence.INFERRED


def test_a_selection_is_immutable_and_extension_returns_a_new_one(corpus):
    base = symbols()
    narrowed = base.where(row.kind.eq(SymbolKind.FUNCTION))
    assert base.stages == ()
    assert narrowed.stages == ("where",)
    assert len(base.rows(corpus)) > len(narrowed.rows(corpus))


# ---------------------------------------------------------------------------
# fork #9: bare callables are refused, opaques are the escape
# ---------------------------------------------------------------------------


def test_where_refuses_a_bare_lambda_and_names_the_escape():
    with pytest.raises(OpaquePredicateError) as caught:
        symbols().where(lambda candidate: True)
    message = str(caught.value)
    assert "bare callable" in message
    assert "@opaque(name, reads=(...))" in message
    assert caught.value.code == "opaque-predicate"


def test_where_refuses_a_named_function_too():
    def looks_respectable(candidate):
        return True

    with pytest.raises(OpaquePredicateError) as caught:
        symbols().where(looks_respectable)
    assert "'looks_respectable'" in str(caught.value)


def test_where_refuses_a_non_expression_non_callable():
    with pytest.raises(TypeError, match="where\\(\\) takes an expression, got int"):
        symbols().where(3)


def test_where_accepts_an_opaque_wrapped_predicate(corpus):
    @opaque("dunder-named", reads=("name",))
    def dunder_named(candidate):
        return candidate.name.startswith("__")

    assert symbols().where(dunder_named).rows(corpus) == ()


def test_opaque_without_a_reads_declaration_is_a_type_error():
    with pytest.raises(TypeError):
        opaque("nameless")


def test_opaque_with_an_empty_reads_declaration_is_refused():
    with pytest.raises(OpaquePredicateError) as caught:
        opaque("nameless", reads=())
    assert "declares no reads" in str(caught.value)
    assert "not inspectable" in str(caught.value)


def test_opaque_declared_reads_are_not_validated_against_the_field_table(corpus):
    """A declaration describes a body the DSL cannot see; validating it would be a lie."""

    @opaque("named-helper", reads=("symbol.name", "whatever the body wants"))
    def named_helper(candidate):
        return candidate.name == "helper"

    assert [m.anchor.id for m in symbols().where(named_helper).rows(corpus)] == [
        "app:helper",
        "lib:helper",
    ]


def test_a_prose_read_survives_a_project_that_drops_every_real_field(corpus):
    """Free-form tokens stay legal after a project: they name nothing to lose."""

    @opaque("named-helper", reads=("whatever the body wants",))
    def named_helper(candidate):
        return candidate.name == "helper"

    assert [
        m.anchor.id for m in symbols().project("name").where(named_helper).rows(corpus)
    ] == ["app:helper", "lib:helper"]


def test_an_opaque_read_that_a_project_dropped_is_refused_not_silently_empty(corpus):
    """The escape hatch may not smuggle a row past fork #12's no-silent-empty rule.

    The body would read the projected-away field as ``None`` and reject every
    row, which is indistinguishable from an honest empty answer. The same
    predicate written as an expression already refuses, and so must this.
    """

    @opaque("is-var", reads=("kind",))
    def is_var(candidate):
        return candidate.kind is SymbolKind.VARIABLE

    projected = symbols().project("name")
    with pytest.raises(UnknownFieldError) as caught:
        projected.where(is_var)
    message = str(caught.value)
    assert "unknown field 'kind' for the symbols universe" in message
    assert "valid fields are: name" in message
    assert "'is-var' declares it in reads=" in message
    assert "reject every row silently" in message
    assert caught.value.as_error_fields() == {
        "code": "unknown-field",
        "message": message,
        "field": "kind",
        "valid": ["name"],
        "declared_by": "is-var",
    }

    # The expression spelling of the same predicate, for the comparison the
    # refusal exists to restore.
    with pytest.raises(UnknownFieldError):
        projected.where(row.kind.eq(SymbolKind.VARIABLE))

    # And unprojected, both spellings still work and agree.
    assert [m.anchor.id for m in symbols().where(is_var).rows(corpus)] == [
        m.anchor.id
        for m in symbols().where(row.kind.eq(SymbolKind.VARIABLE)).rows(corpus)
    ]


def test_an_opaque_read_still_visible_after_a_project_is_accepted(corpus):
    @opaque("is-var", reads=("kind",))
    def is_var(candidate):
        return candidate.kind is SymbolKind.VARIABLE

    kept = symbols().project("name", "kind").where(is_var).rows(corpus)
    assert [m.anchor.id for m in kept] == ["app:go:items", "app:_private"]


# ---------------------------------------------------------------------------
# derived reach
# ---------------------------------------------------------------------------


def test_a_plain_selection_reaches_only_the_file():
    assert symbols().where(row.kind.eq(SymbolKind.FUNCTION)).reach is Reach.FILE


def test_following_a_resolver_backed_step_derives_project_reach():
    assert symbols().follow("definition").reach is Reach.PROJECT
    assert references().follow("definition").reach is Reach.PROJECT
    assert imports().follow("target").reach is Reach.PROJECT


def test_file_local_follow_steps_stay_file_reach():
    assert symbols().follow("references").reach is Reach.FILE
    assert symbols().follow("scope").follow("symbols").reach is Reach.FILE
    assert modules().follow("imports").reach is Reach.FILE


def test_an_opaque_declaring_a_project_read_lifts_the_whole_selection():
    @opaque("crosses-files", reads=("project:resolver",))
    def crosses_files(candidate):
        return True

    assert symbols().where(crosses_files).reach is Reach.PROJECT


def test_reach_cannot_be_declared():
    selection = symbols()
    assert "reach" not in Selection.__init__.__code__.co_varnames
    with pytest.raises(AttributeError):
        selection.reach = Reach.PROJECT


def test_reach_survives_a_later_file_local_stage():
    assert symbols().follow("definition").follow("references").reach is Reach.PROJECT


# ---------------------------------------------------------------------------
# follow semantics
# ---------------------------------------------------------------------------


def test_follow_switches_universe_and_restores_its_field_set(corpus):
    followed = symbols().follow("scope")
    assert followed.universe == "scopes"
    assert followed.fields == frozenset(universe_fields("scopes"))
    assert all(m.anchor.kind.value == "scope" for m in followed.rows(corpus))


def test_follow_yields_distinct_targets_in_first_reached_order(corpus):
    reached = [m.anchor.id for m in symbols().follow("module").rows(corpus)]
    assert reached == ["app", "lib"]


def test_follow_definition_crosses_files(corpus):
    reached = [
        m.anchor.id
        for m in imports().where(row.name.eq("helper")).follow("target").rows(corpus)
    ]
    assert reached == ["lib:helper"]


def test_following_a_heuristic_import_keeps_the_target_heuristic(indexed_project):
    _, store = indexed_project({
        "lib.py": LIB,
        "app.py": 'import importlib\nhelper = importlib.import_module("lib")\n',
    })
    corpus = Corpus(store)
    reached = imports().where(row.imported_from.eq("lib")).follow("target").rows(corpus)
    assert [m.anchor.id for m in reached] == ["lib"]
    assert reached[0].anchor.evidence is Confidence.HEURISTIC
    assert reached[0].confidence is Confidence.HEURISTIC


def test_follow_scope_symbols_lists_only_direct_members(corpus):
    members = symbols().where(row.symbol_id.eq("app:Widget")).follow("scope")
    assert [m.anchor.id for m in members.rows(corpus)] == ["app"]

    inner = scopes().where(row.scope_id.eq("app:Widget")).follow("symbols")
    assert [m.anchor.id for m in inner.rows(corpus)] == ["app:Widget.paint"]


def test_unknown_follow_step_lists_the_valid_ones():
    with pytest.raises(UnknownFollowError) as caught:
        symbols().follow("definitions")
    message = str(caught.value)
    assert "unknown follow step 'definitions' from the symbols universe" in message
    for step in universe_follows("symbols"):
        assert step in message
    assert caught.value.code == "unknown-follow"
    assert caught.value.as_error_fields()["valid"] == sorted(universe_follows("symbols"))


def test_follow_steps_differ_per_universe():
    assert universe_follows("imports") == ("module", "target")
    assert universe_follows("scopes") == ("parent", "symbols")
    with pytest.raises(UnknownFollowError):
        scopes().follow("definition")


# ---------------------------------------------------------------------------
# project, and written order made observable
# ---------------------------------------------------------------------------


def test_project_narrows_the_returned_fields(corpus):
    narrowed = symbols().where(row.symbol_id.eq("lib:helper")).project("name", "line")
    (match,) = narrowed.rows(corpus)
    assert match.fields == {"name": "helper", "line": 1}


def test_a_where_after_a_project_cannot_read_a_dropped_field():
    with pytest.raises(UnknownFieldError) as caught:
        symbols().project("name").where(row.kind.eq(SymbolKind.FUNCTION))
    message = str(caught.value)
    assert "unknown field 'kind' for the symbols universe" in message
    assert "valid fields are: name" in message


def test_the_same_two_stages_in_the_other_order_are_fine(corpus):
    ordered = symbols().where(row.kind.eq(SymbolKind.FUNCTION)).project("name")
    assert {m.fields["name"] for m in ordered.rows(corpus)} == {"go", "helper"}


def test_project_refuses_a_field_the_universe_does_not_declare():
    with pytest.raises(UnknownFieldError) as caught:
        modules().project("symbol_id")
    assert "unknown field 'symbol_id' for the modules universe" in str(caught.value)
    assert caught.value.as_error_fields()["valid"] == ["file_path", "has_errors", "module"]


def test_a_follow_restores_fields_a_project_had_dropped(corpus):
    restored = symbols().project("symbol_id").follow("module").where(row.module.is_true())
    assert [m.anchor.id for m in restored.rows(corpus)] == ["app", "lib"]


# ---------------------------------------------------------------------------
# loud authoring errors
# ---------------------------------------------------------------------------


def test_unknown_field_names_every_valid_field():
    with pytest.raises(UnknownFieldError) as caught:
        references().where(row.visibility.eq("public"))
    message = str(caught.value)
    assert "unknown field 'visibility' for the references universe" in message
    for name in universe_fields("references"):
        assert name in message
    assert caught.value.code == "unknown-field"


def test_unknown_universe_is_its_own_error():
    with pytest.raises(UnknownUniverseError) as caught:
        Selection("functions")
    assert str(caught.value) == (
        "unknown universe 'functions'; the universes are: "
        "imports, modules, references, scopes, symbols"
    )
    assert caught.value.code == "unknown-universe"


def test_an_unregistered_trait_is_loud_at_evaluation(corpus):
    with pytest.raises(UnknownTraitError) as caught:
        symbols().where(trait_of("no-such-trait").value.is_true()).rows(corpus)
    assert "no trait provider is registered under 'no-such-trait'" in str(caught.value)
    assert caught.value.code == "unknown-trait"


def test_an_opaque_declaring_an_unregistered_trait_is_loud_too(corpus):
    @opaque("wants-a-ghost", reads=("trait:no-such-trait",))
    def wants_a_ghost(candidate):
        return True

    with pytest.raises(UnknownTraitError):
        symbols().where(wants_a_ghost).rows(corpus)


# ---------------------------------------------------------------------------
# traits read through a selection
# ---------------------------------------------------------------------------


def test_a_trait_assertion_selects_and_reports_declared(corpus):
    matched = symbols().where(
        trait_of("type-annotation").at(Confidence.INFERRED).eq("list")
    ).rows(corpus)
    assert [m.anchor.id for m in matched] == ["app:go:items"]
    assert matched[0].confidence is Confidence.DECLARED


def test_the_uncertified_spelling_of_the_same_query_reports_inferred(corpus):
    matched = symbols().where(trait_of("type-annotation").value.eq("list")).rows(corpus)
    assert [m.anchor.id for m in matched] == ["app:go:items"]
    assert matched[0].confidence is Confidence.INFERRED


def test_a_trait_is_only_resolved_for_rows_that_reach_it(corpus):
    """The trait clause is written second, so rows the first clause rejects never pay for it."""
    resolved: list[str] = []

    @opaque("watch", reads=("symbol_id",))
    def watch(candidate):
        resolved.append(candidate.symbol_id)
        return candidate.symbol_id == "app:go:items"

    matched = symbols().where(watch).where(
        trait_of("type-annotation").value.eq("list")
    ).rows(corpus)
    assert [m.anchor.id for m in matched] == ["app:go:items"]
    assert len(resolved) > 1


def test_match_confidence_meets_row_evidence_with_the_derivation(indexed_project):
    _, store = indexed_project({
        "app.py": 'import importlib\nx = importlib.import_module("json")\n',
    })
    corpus = Corpus(store)
    (match,) = imports().where(row.imported_from.eq("json")).rows(corpus)
    assert match.confidence is Confidence.HEURISTIC
    assert len(match.derivations) == 1
    assert match.derivations[0].confidence is Confidence.DECLARED


def test_each_where_stage_contributes_one_derivation(corpus):
    (match,) = (
        symbols()
        .where(row.name.eq("helper"))
        .where(row.kind.eq(SymbolKind.FUNCTION))
        .rows(corpus)
    )
    assert len(match.derivations) == 2
    assert [node.op for node in match.derivations] == ["compare.eq", "compare.eq"]


# ---------------------------------------------------------------------------
# the corpus
# ---------------------------------------------------------------------------


def test_src_roots_filter_which_files_a_selection_can_see(indexed_project):
    _, store = indexed_project({"src/lib.py": LIB, "scripts/tool.py": "def run():\n    pass\n"})
    scoped = Corpus(store, ("src",))
    assert [m.fields["file_path"] for m in modules().rows(scoped)] == ["src/lib.py"]
    assert [m.fields["file_path"] for m in modules().rows(Corpus(store))] == [
        "scripts/tool.py",
        "src/lib.py",
    ]


def test_src_roots_match_on_path_segments_not_bare_prefixes(indexed_project):
    _, store = indexed_project({"src/lib.py": LIB, "srcextra/other.py": LIB})
    scoped = Corpus(store, ("src",))
    assert [m.fields["file_path"] for m in modules().rows(scoped)] == ["src/lib.py"]
    assert scoped.src_roots == ("src/",)


def test_corpus_locate_finds_a_symbol_with_its_index(corpus):
    found = corpus.locate("lib:helper")
    assert found is not None
    symbol, index = found
    assert symbol.name == "helper"
    assert index.file_path == "lib.py"
    assert corpus.locate("lib:nonexistent") is None
