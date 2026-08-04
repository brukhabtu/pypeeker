"""Evidence-typed anchors: exactly one match, or a loud structured refusal with candidates.

Fork #12's whole point is that "nothing matched your anchor" and "your predicate
selected nothing" must never be the same answer. These tests assert the
refusals by code, by wording, and by the payload a CLI would splat into its
error envelope.
"""

import json

import pytest

from pypeeker.dsl import (
    MAX_ANCHOR_CANDIDATES,
    AmbiguousAnchorError,
    Anchor,
    AnchorKind,
    AnchorError,
    Corpus,
    UnresolvedAnchorError,
    resolve_symbol_anchor,
)
from pypeeker.models import Confidence

LIB = """\
def helper():
    return 1


class Widget:
    def paint(self):
        return None
"""

APP = """\
from lib import helper


def go():
    return helper()
"""


@pytest.fixture
def corpus(indexed_project):
    """A two-file corpus with one deliberately duplicated leaf name (``helper``)."""
    _, store = indexed_project({"lib.py": LIB, "app.py": APP})
    return Corpus(store)


# ---------------------------------------------------------------------------
# resolution
# ---------------------------------------------------------------------------


def test_an_exact_id_resolves_to_declared_evidence(corpus):
    anchor = resolve_symbol_anchor(corpus, "lib:helper")
    assert anchor == Anchor(AnchorKind.SYMBOL, "lib:helper", Confidence.DECLARED)


def test_a_cli_typed_id_is_declared_by_default(corpus):
    """Fork #12: an id the user typed is declared evidence — nothing was inferred."""
    assert resolve_symbol_anchor(corpus, "lib:Widget.paint").evidence is Confidence.DECLARED


def test_an_unambiguous_tail_resolves_to_the_full_id(corpus):
    anchor = resolve_symbol_anchor(corpus, "Widget.paint")
    assert anchor.id == "lib:Widget.paint"
    assert anchor.kind is AnchorKind.SYMBOL


def test_evidence_can_be_stated_weaker_for_a_navigated_anchor(corpus):
    anchor = resolve_symbol_anchor(corpus, "lib:helper", evidence=Confidence.HEURISTIC)
    assert anchor.evidence is Confidence.HEURISTIC
    assert anchor.id == "lib:helper"


def test_an_exact_id_wins_over_the_partials_it_is_a_tail_of(indexed_project):
    """Typing the canonical id must never be reported as ambiguous."""
    _, store = indexed_project({
        "lib.py": "def helper():\n    return 1\n",
        "pkg.py": "helper = 1\n",
    })
    corpus = Corpus(store)
    assert resolve_symbol_anchor(corpus, "lib:helper").id == "lib:helper"


def test_resolution_is_confined_to_the_corpus(indexed_project):
    """An anchor may only name a row the selection could actually produce."""
    _, store = indexed_project({"src/lib.py": LIB, "scripts/tool.py": "def helper():\n    pass\n"})
    scoped = Corpus(store, ("src",))
    assert resolve_symbol_anchor(scoped, "helper").id == "src.lib:helper"


# ---------------------------------------------------------------------------
# unresolved
# ---------------------------------------------------------------------------


def test_an_unknown_anchor_raises_rather_than_returning_nothing(corpus):
    with pytest.raises(UnresolvedAnchorError) as caught:
        resolve_symbol_anchor(corpus, "lib:nonexistent")
    assert caught.value.code == "unresolved-anchor"
    assert caught.value.anchor == "lib:nonexistent"


def test_an_unresolved_anchor_suggests_a_tail_match(corpus):
    with pytest.raises(UnresolvedAnchorError) as caught:
        resolve_symbol_anchor(corpus, "src/lib.py:helper")
    assert set(caught.value.candidates) == {"app:helper", "lib:helper"}
    assert "did you mean: app:helper, lib:helper" in str(caught.value)


def test_an_unresolved_anchor_with_no_near_miss_carries_no_candidates(corpus):
    with pytest.raises(UnresolvedAnchorError) as caught:
        resolve_symbol_anchor(corpus, "zzz:qqq")
    assert caught.value.candidates == ()
    assert "did you mean" not in str(caught.value)
    assert str(caught.value) == "no symbol matches anchor 'zzz:qqq'"


def test_candidate_search_reaches_outside_the_corpus_to_stay_useful(indexed_project):
    """A symbol outside the src roots is still worth naming as a near miss."""
    _, store = indexed_project({
        "src/lib.py": "def inside():\n    pass\n",
        "scripts/tool.py": "def outside():\n    pass\n",
    })
    scoped = Corpus(store, ("src",))
    with pytest.raises(UnresolvedAnchorError) as caught:
        resolve_symbol_anchor(scoped, "outside")
    assert caught.value.candidates == ("scripts.tool:outside",)


def test_an_exactly_indexed_id_outside_the_roots_is_diagnosed_not_denied(indexed_project):
    """"No symbol matches" would be a false statement about the index."""
    _, store = indexed_project({
        "src/lib.py": "def inside():\n    pass\n",
        "scripts/tool.py": "def outside():\n    pass\n",
    })
    scoped = Corpus(store, ("src",))
    with pytest.raises(UnresolvedAnchorError) as caught:
        resolve_symbol_anchor(scoped, "scripts.tool:outside")
    assert str(caught.value) == (
        "anchor 'scripts.tool:outside' names an indexed symbol that lies outside "
        "the configured source roots, so no selection reaches it; add its "
        "directory to [tool.pypeeker] src, or anchor on a symbol inside the roots"
    )
    assert caught.value.code == "unresolved-anchor"
    assert "no symbol matches" not in str(caught.value)


def test_the_anchor_is_never_offered_back_as_its_own_candidate(indexed_project):
    """A suggestion identical to the input loops a caller that follows suggestions."""
    _, store = indexed_project({
        "src/lib.py": "def inside():\n    pass\n",
        "scripts/tool.py": "def outside():\n    pass\n",
    })
    scoped = Corpus(store, ("src",))
    with pytest.raises(UnresolvedAnchorError) as caught:
        resolve_symbol_anchor(scoped, "scripts.tool:outside")
    assert "scripts.tool:outside" not in caught.value.candidates
    assert caught.value.candidates == ()


def test_following_a_suggestion_reaches_a_different_error_not_the_same_one(indexed_project):
    """The one-hop-earlier trap: a bare name suggests an id, and that id must move."""
    _, store = indexed_project({
        "src/lib.py": "def inside():\n    pass\n",
        "scripts/tool.py": "def outside():\n    pass\n",
    })
    scoped = Corpus(store, ("src",))
    with pytest.raises(UnresolvedAnchorError) as first:
        resolve_symbol_anchor(scoped, "outside")
    (suggested,) = first.value.candidates
    assert suggested == "scripts.tool:outside"
    with pytest.raises(UnresolvedAnchorError) as second:
        resolve_symbol_anchor(scoped, suggested)
    assert str(second.value) != str(first.value)
    assert "outside the configured source roots" in str(second.value)


def test_candidates_are_capped(indexed_project):
    files = {
        f"m{i}.py": "def crowded():\n    pass\n" for i in range(MAX_ANCHOR_CANDIDATES + 5)
    }
    _, store = indexed_project(files)
    corpus = Corpus(store)
    with pytest.raises(UnresolvedAnchorError) as caught:
        resolve_symbol_anchor(corpus, "nowhere.py:crowded")
    assert len(caught.value.candidates) == MAX_ANCHOR_CANDIDATES


# ---------------------------------------------------------------------------
# ambiguous
# ---------------------------------------------------------------------------


def test_an_ambiguous_anchor_raises_and_lists_every_match(corpus):
    with pytest.raises(AmbiguousAnchorError) as caught:
        resolve_symbol_anchor(corpus, "helper")
    assert caught.value.code == "ambiguous-anchor"
    assert caught.value.candidates == ("app:helper", "lib:helper")
    assert str(caught.value) == (
        "anchor 'helper' is ambiguous; it matches: app:helper, lib:helper. "
        "Pass one of these ids instead."
    )


def test_ambiguity_is_never_silently_resolved_to_the_first_match(corpus):
    with pytest.raises(AmbiguousAnchorError):
        resolve_symbol_anchor(corpus, "helper")


# ---------------------------------------------------------------------------
# the payload a CLI hands on
# ---------------------------------------------------------------------------


def test_both_failures_share_the_anchor_error_base(corpus):
    for raw in ("lib:nonexistent", "helper"):
        with pytest.raises(AnchorError):
            resolve_symbol_anchor(corpus, raw)


def test_error_fields_are_shaped_for_the_cli_error_sink(corpus):
    with pytest.raises(UnresolvedAnchorError) as caught:
        resolve_symbol_anchor(corpus, "src/lib.py:helper")
    fields = caught.value.as_error_fields()
    assert fields["code"] == "unresolved-anchor"
    assert fields["anchor"] == "src/lib.py:helper"
    assert sorted(fields["candidates"]) == ["app:helper", "lib:helper"]
    assert fields["message"] == str(caught.value)
    # plain json.dumps, no `default=` fallback: the payload is already JSON-safe.
    assert json.loads(json.dumps(fields))["code"] == "unresolved-anchor"


def test_ambiguous_error_fields_carry_every_candidate(corpus):
    with pytest.raises(AmbiguousAnchorError) as caught:
        resolve_symbol_anchor(corpus, "helper")
    fields = caught.value.as_error_fields()
    assert fields == {
        "code": "ambiguous-anchor",
        "message": str(caught.value),
        "anchor": "helper",
        "candidates": ["app:helper", "lib:helper"],
    }


# ---------------------------------------------------------------------------
# the anchor value itself
# ---------------------------------------------------------------------------


def test_anchor_is_frozen_and_hashable():
    anchor = Anchor(AnchorKind.SYMBOL, "lib:helper")
    assert {anchor, Anchor(AnchorKind.SYMBOL, "lib:helper")} == {anchor}
    with pytest.raises(AttributeError):
        anchor.id = "other"


def test_anchor_defaults_to_declared_evidence():
    assert Anchor(AnchorKind.MODULE, "lib").evidence is Confidence.DECLARED


def test_with_evidence_restates_without_mutating():
    anchor = Anchor(AnchorKind.SYMBOL, "lib:helper")
    weaker = anchor.with_evidence(Confidence.HEURISTIC)
    assert weaker == Anchor(AnchorKind.SYMBOL, "lib:helper", Confidence.HEURISTIC)
    assert anchor.evidence is Confidence.DECLARED


def test_anchor_kinds_cover_the_five_universes():
    assert {kind.value for kind in AnchorKind} == {
        "symbol",
        "reference",
        "import",
        "module",
        "scope",
    }
