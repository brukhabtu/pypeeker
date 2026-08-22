"""demote and privatize: two selections over one shared mutation value.

This is fork #2's acceptance test. The frozen tree carries two implementations
of one operation with two intent kinds and two confidence floors; here there is
one :data:`~pypeeker.dsl.DEMOTE` value, and the two entry points differ only in
which rows they select. The tests that matter are the two that would be
impossible to write if that were merely a convention:

* :func:`test_both_entry_points_reach_the_same_mutation_object` — object
  identity, not equality. Two structurally-identical mutation values would
  satisfy equality and still be the drift the fork forbids.
* :func:`test_a_cli_typed_id_applies_where_a_heuristic_finding_refuses` — the
  same symbol, the same mutation, the same floor: refused through the
  nomination rules because dynamic access weakens the row, applied through a
  typed anchor because fork #12 makes a typed id ``DECLARED``.
"""

import pytest

from pypeeker.dsl import (
    DEMOTE,
    DEMOTE_ORIGIN_CLI,
    DEMOTION_RULES,
    Corpus,
    demote_selection,
    privatize_selections,
    resolve_symbol_anchor,
)
from pypeeker.intents import ChangeVisibilityIntent
from pypeeker.models import Confidence

# `getattr(obj, name)` anywhere in a module is what the frozen engine calls
# dynamic access: it may consume any public symbol invisibly, so every
# visibility finding in the module drops to HEURISTIC.
DYNAMIC = '''"""A module that reaches for attributes by name."""


def unreferenced_thing():
    """Nothing in the project calls this."""
    return 1


def fetch(obj, name):
    """Read an attribute the binder cannot see."""
    return getattr(obj, name)
'''

PLAIN = '''"""A module with no dynamic access at all."""


def unreferenced_thing():
    """Nothing in the project calls this."""
    return 1
'''


@pytest.fixture
def corpus_of(indexed_project):
    """Build a corpus with no source-root filter, so module ids are bare."""

    def _build(files):
        _, store = indexed_project(files)
        return Corpus(store, ())

    return _build


def privatize_decisions(corpus, options=None):
    """Every nomination the three rules make, as ``(rule_id, decision)`` pairs."""
    found = []
    for rule_id, selection in privatize_selections(options or {}):
        for decision in selection.decisions(corpus, rule_id):
            found.append((rule_id, decision))
    return found


# ---------------------------------------------------------------------------
# one operation, one mutation value
# ---------------------------------------------------------------------------


def test_both_entry_points_reach_the_same_mutation_object(corpus_of):
    # Identity, deliberately. Two structurally equal Mutation values would pass
    # an `==` check and still be exactly the two-implementations drift fork #2
    # exists to make unwritable.
    corpus = corpus_of({"app.py": PLAIN})
    anchor = resolve_symbol_anchor(corpus, "app:unreferenced_thing")
    typed = demote_selection(anchor)
    nominated = privatize_selections({})[0][1]
    assert typed.mutation is DEMOTE
    assert nominated.mutation is DEMOTE
    assert typed.mutation is nominated.mutation


def test_the_confidence_floor_is_an_attribute_of_the_shared_value():
    # There is exactly one floor because there is exactly one value carrying
    # it; nothing in either entry point can state a different one.
    assert DEMOTE.floor is Confidence.INFERRED
    assert not hasattr(demote_selection, "floor")


def test_the_application_operator_is_reached_the_same_way_from_both_sides(corpus_of):
    corpus = corpus_of({"app.py": PLAIN})
    anchor = resolve_symbol_anchor(corpus, "app:unreferenced_thing")
    assert type(demote_selection(anchor)) is type(
        privatize_selections({})[0][1]
    )


def test_the_three_nomination_rules_are_the_frozen_workflows_three(corpus_of):
    assert DEMOTION_RULES == (
        "over-exposed-module-symbol",
        "unused-public-symbol",
        "test-only-production-code",
    )
    assert [rule_id for rule_id, _ in privatize_selections({})] == list(DEMOTION_RULES)


# ---------------------------------------------------------------------------
# AC #1: a CLI-typed id applies where a heuristic finding refuses
# ---------------------------------------------------------------------------


def test_a_heuristic_nomination_produces_no_intent(corpus_of):
    corpus = corpus_of({"app.py": DYNAMIC})
    decisions = [d for _, d in privatize_decisions(corpus)]
    nominated = [d for d in decisions if d.match.anchor.id == "app:unreferenced_thing"]
    assert nominated, "the symbol must actually be nominated, or this proves nothing"
    assert {d.match.confidence for d in nominated} == {Confidence.HEURISTIC}
    assert [d.intent for d in nominated] == [None] * len(nominated)
    assert {d.reason for d in nominated} == {"below-floor"}


def test_a_cli_typed_id_applies_where_a_heuristic_finding_refuses(corpus_of):
    # THE acceptance criterion. Same symbol, same corpus, same DEMOTE value,
    # same floor — and the answers differ, because fork #12 makes a typed id
    # DECLARED evidence and that evidence is a real operand of the comparison.
    corpus = corpus_of({"app.py": DYNAMIC})
    anchor = resolve_symbol_anchor(corpus, "app:unreferenced_thing")
    assert anchor.evidence is Confidence.DECLARED

    intents = demote_selection(anchor).intents(corpus, DEMOTE_ORIGIN_CLI)
    assert intents == (
        ChangeVisibilityIntent(
            "cli:demote:app:unreferenced_thing",
            symbol_id="app:unreferenced_thing",
            direction="demote",
        ),
    )


def test_a_weakened_anchor_falls_below_the_floor_on_the_typed_path_too(corpus_of):
    # What makes the Const load-bearing rather than ceremonial: with DECLARED
    # evidence the comparison contributes the lattice identity and the typed
    # path would behave identically with a bare string rhs. Weaken the anchor
    # and the difference becomes visible — the anchor's evidence really is
    # meeting into Match.confidence.
    corpus = corpus_of({"app.py": PLAIN})
    shaky = resolve_symbol_anchor(
        corpus, "app:unreferenced_thing", evidence=Confidence.HEURISTIC
    )
    decisions = demote_selection(shaky).decisions(corpus, DEMOTE_ORIGIN_CLI)
    assert [d.reason for d in decisions] == ["below-floor"]
    assert demote_selection(shaky).intents(corpus, DEMOTE_ORIGIN_CLI) == ()


def test_the_same_anchor_at_declared_evidence_applies(corpus_of):
    corpus = corpus_of({"app.py": PLAIN})
    firm = resolve_symbol_anchor(
        corpus, "app:unreferenced_thing", evidence=Confidence.DECLARED
    )
    assert len(demote_selection(firm).intents(corpus, DEMOTE_ORIGIN_CLI)) == 1


# ---------------------------------------------------------------------------
# the intents that come out: flat, unordered, one existing planner kind
# ---------------------------------------------------------------------------


def test_a_nominated_symbol_with_no_dynamic_access_yields_a_change_visibility_intent(
    corpus_of,
):
    corpus = corpus_of({"app.py": PLAIN})
    by_rule = {}
    for rule_id, selection in privatize_selections({}):
        by_rule[rule_id] = selection.intents(corpus, rule_id)
    produced = [i for intents in by_rule.values() for i in intents]
    assert produced, "the plain module must nominate something"
    assert {type(i) for i in produced} == {ChangeVisibilityIntent}
    assert {i.direction for i in produced} == {"demote"}


def test_the_derived_id_names_the_rule_that_nominated_the_symbol(corpus_of):
    corpus = corpus_of({"app.py": PLAIN})
    ids = set()
    for rule_id, selection in privatize_selections({}):
        ids.update(i.intent_id for i in selection.intents(corpus, rule_id))
    assert "unused-public-symbol:demote:app:unreferenced_thing" in ids


def test_intents_is_flat_and_carries_no_ordering_of_its_own(corpus_of):
    corpus = corpus_of({"app.py": PLAIN, "other.py": PLAIN})
    _, selection = privatize_selections({})[1]
    application = selection
    intents = application.intents(corpus, "unused-public-symbol")
    assert isinstance(intents, tuple)
    assert all(not isinstance(i, (list, tuple)) for i in intents)
    # Row order, not sorted order: the batch scheduler owns ordering, and a
    # DSL that pre-sorted would be a second scheduler disagreeing with it.
    assert [i.intent_id for i in intents] == [
        d.intent.intent_id
        for d in application.decisions(corpus, "unused-public-symbol")
        if d.intent is not None
    ]


def test_an_already_private_symbol_is_refused_with_the_frozen_reason_code(corpus_of):
    corpus = corpus_of({
        "app.py": '"""App."""\n\n\ndef _helper():\n    """Already private."""\n    return 1\n'
    })
    anchor = resolve_symbol_anchor(corpus, "app:_helper")
    decisions = demote_selection(anchor).decisions(corpus, DEMOTE_ORIGIN_CLI)
    assert [d.reason for d in decisions] == ["already-private"]


def test_main_is_never_demoted(corpus_of):
    corpus = corpus_of({
        "app.py": '"""App."""\n\n\ndef main():\n    """Entry point."""\n    return 1\n'
    })
    anchor = resolve_symbol_anchor(corpus, "app:main")
    decisions = demote_selection(anchor).decisions(corpus, DEMOTE_ORIGIN_CLI)
    assert [d.reason for d in decisions] == ["dunder-or-main"]


def test_the_symbol_id_comes_off_the_anchor_and_never_out_of_a_message(corpus_of):
    # The frozen check/demotion.py recovers each candidate id by running three
    # regexes over the finding's message text, which makes rule wording a
    # contract. Nothing here reads a message: the id on the intent is exactly
    # the row's anchor id.
    corpus = corpus_of({"app.py": PLAIN})
    for rule_id, selection in privatize_selections({}):
        for decision in selection.decisions(corpus, rule_id):
            if decision.intent is not None:
                assert decision.intent.symbol_id == decision.match.anchor.id
