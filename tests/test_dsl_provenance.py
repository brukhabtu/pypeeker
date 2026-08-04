"""``--why``: the derivation tree as versioned, additive-only, provenance-free JSON.

Three properties, each of which a reviewer would otherwise have to take on
trust:

* **Versioned, and additive-only.** The required keys are asserted as a
  *subset* of every node's keys, never an equality, so adding a key later
  cannot break these tests — which is exactly the contract "additive-only"
  makes to consumers.
* **JSON-safe without help.** Serialized with a plain ``json.dumps`` and no
  ``default=``, because ``cli.py``'s error sink passes ``default=str`` and
  would silently stringify anything unserializable — a test leaning on that
  would prove nothing.
* **Provenance-free.** :mod:`pypeeker.analysis.traits` forbids
  ``Trait.provenance`` from ever reaching output; fork #7 requires ``--why``
  *in* output. The reconciliation is that they are different objects, and the
  last section here walks the whole tree to prove the prose never leaks in.
"""

from __future__ import annotations

import json

import pytest

from pypeeker.analysis import TYPE_ANNOTATION, VARIABLE_MUTATION, get_trait_provider
from pypeeker.dsl import (
    PROVENANCE_SCHEMA,
    TUPLE_CANDIDATE,
    UNMATCHED_JSON,
    Corpus,
    Derivation,
    EvalContext,
    derivation_document,
    derivation_to_dict,
    row,
    symbols,
    trait_of,
)
from pypeeker.dsl.selection import _LazyTraits
from pypeeker.dsl.universes import _symbol_fields
from pypeeker.models import Confidence

CLEAN = """\
def f():
    xs = []
    return xs[0]
"""

REQUIRED_NODE_KEYS = frozenset({"op", "value", "confidence", "reads", "inputs"})


@pytest.fixture
def corpus(indexed_project):
    """A one-file corpus whose single list local is a clean tuple candidate."""
    _, store = indexed_project({"src/m.py": CLEAN})
    return Corpus(store, ("src",))


@pytest.fixture
def match(corpus):
    """The one ``tuple-candidate`` match, carrying its derivations."""
    matched = symbols().where(TUPLE_CANDIDATE).rows(corpus)
    assert [m.anchor.id for m in matched] == ["src.m:f:xs"]
    return matched[0]


def _walk(node: dict) -> list[dict]:
    """Every node of a serialized derivation tree, root first."""
    found = [node]
    for child in node["inputs"]:
        found.extend(_walk(child))
    return found


# ---------------------------------------------------------------------------
# the version
# ---------------------------------------------------------------------------


def test_the_schema_version_is_one():
    assert PROVENANCE_SCHEMA == 1


def test_the_document_stamps_the_version_at_its_root(match):
    document = derivation_document(match.derivations)
    assert document["schema"] == PROVENANCE_SCHEMA


def test_the_document_carries_one_tree_per_written_clause(match):
    document = derivation_document(match.derivations)
    assert len(document["derivations"]) == len(match.derivations) == 1


def test_the_document_of_no_derivations_is_still_versioned():
    assert derivation_document(()) == {"schema": PROVENANCE_SCHEMA, "derivations": []}


# ---------------------------------------------------------------------------
# additive-only shape
# ---------------------------------------------------------------------------


def test_every_node_carries_at_least_the_required_keys(match):
    for node in _walk(derivation_to_dict(match.derivations[0])):
        # Subset, never equality: a future key must not break a consumer, and a
        # test asserting equality here would make adding one a breaking change.
        assert REQUIRED_NODE_KEYS <= set(node), node["op"]


def test_op_specific_detail_is_flattened_alongside_the_required_keys(match):
    nodes = {node["op"]: node for node in _walk(derivation_to_dict(match.derivations[0]))}
    assert nodes["field"]["field"] in {"kind", "scope_kind"}
    assert nodes["trait.at"]["trait"] == TYPE_ANNOTATION
    assert nodes["trait.at"]["level"] == "inferred"
    assert nodes["trait.at"]["matched"] is True
    assert nodes["compare.eq"]["comparison"] == "eq"


def test_detail_keys_can_never_shadow_a_required_key():
    shadowing = Derivation(
        op="synthetic",
        value=1,
        confidence=Confidence.DECLARED,
        detail={"op": "hijacked", "value": "hijacked", "inputs": "hijacked"},
    )
    rendered = derivation_to_dict(shadowing)
    assert rendered["op"] == "synthetic"
    assert rendered["value"] == 1
    assert rendered["inputs"] == []


def test_confidence_renders_as_its_string_value(match):
    for node in _walk(derivation_to_dict(match.derivations[0])):
        assert node["confidence"] in {"declared", "inferred", "heuristic", "unknown"}


def test_the_root_node_reports_the_declared_confidence_the_ledger_requires(match):
    rendered = derivation_to_dict(match.derivations[0])
    assert rendered["op"] == "all_of"
    assert rendered["value"] is True
    assert rendered["confidence"] == "declared"


def test_reads_are_sorted_prefixed_tokens(match):
    rendered = derivation_to_dict(match.derivations[0])
    assert rendered["reads"] == sorted(rendered["reads"])
    assert rendered["reads"] == [
        "field:kind",
        "field:scope_kind",
        f"trait:{TYPE_ANNOTATION}",
        f"trait:{VARIABLE_MUTATION}",
    ]


# ---------------------------------------------------------------------------
# JSON safety, without a default= crutch
# ---------------------------------------------------------------------------


def test_the_document_serializes_with_a_plain_json_dumps(match):
    # No default=: cli.py's error sink passes default=str, so a test relying on
    # it would pass for a payload the real query envelope cannot emit.
    encoded = json.dumps(derivation_document(match.derivations))
    assert json.loads(encoded)["schema"] == PROVENANCE_SCHEMA


def test_an_unmatched_assertion_is_selected_out_entirely(corpus):
    # DECLARED, not INFERRED, so the assertion does not hold and the row is gone.
    matched = symbols().where(
        row.symbol_id.eq("src.m:f:xs")
        & trait_of(TYPE_ANNOTATION).at(Confidence.DECLARED).eq("list")
    ).rows(corpus)
    assert matched == ()


def test_an_unmatched_assertion_keeps_its_own_rendering(bind_source):
    # The UNMATCHED sentinel must not collapse into null: null is already a
    # legitimate trait value, the one type-annotation yields for an unannotated
    # symbol, so the two would become indistinguishable in --why.
    index = bind_source(CLEAN, "m.py")
    context = EvalContext(
        fields=_symbol_fields(index, "m:f:xs"),
        traits=_LazyTraits((TYPE_ANNOTATION,), index, "m:f:xs"),
    )
    assertion = trait_of(TYPE_ANNOTATION).at(Confidence.DECLARED)
    rendered = derivation_to_dict(assertion.evaluate(context))
    assert rendered["value"] == UNMATCHED_JSON
    assert rendered["matched"] is False
    assert rendered["confidence"] == "declared"
    json.dumps(rendered)


def test_a_trait_value_object_renders_without_becoming_unserializable(match):
    nodes = {node["op"]: node for node in _walk(derivation_to_dict(match.derivations[0]))}
    # variable-mutation's value is a small frozen record, not a scalar; it falls
    # back to repr rather than failing to encode.
    assert "VariableMutation(" in nodes["trait.value"]["value"]
    json.dumps(nodes["trait.value"])


# ---------------------------------------------------------------------------
# the guardrail: Trait.provenance never reaches output
# ---------------------------------------------------------------------------


def test_no_node_carries_the_trait_provenance_string(match, corpus):
    index = next(i for i in corpus.indexes if i.file_path == "src/m.py")
    prose = {
        get_trait_provider(name)(index, "src.m:f:xs").provenance
        for name in (TYPE_ANNOTATION, VARIABLE_MUTATION)
    }
    assert all(text for text in prose), "the providers must actually produce provenance"
    encoded = json.dumps(derivation_document(match.derivations))
    for text in prose:
        assert text not in encoded


def test_no_node_carries_even_a_fragment_of_the_provenance_convention(match):
    encoded = json.dumps(derivation_document(match.derivations))
    # The convention's opening is the provider's dotted module path; its
    # presence anywhere in the tree would mean a Trait leaked in whole.
    assert "pypeeker.analysis.type_annotation:" not in encoded
    assert "pypeeker.analysis.variable_mutation:" not in encoded
    assert "pypeeker.dsl.naming:" not in encoded


def test_a_trait_reaching_the_serializer_is_projected_past_its_provenance():
    from pypeeker.analysis import Trait

    leaked = Trait(value="list", confidence=Confidence.INFERRED, provenance="SECRET PROSE")
    rendered = derivation_to_dict(
        Derivation(op="synthetic", value=leaked, confidence=Confidence.DECLARED)
    )
    assert rendered["value"] == {"value": "list", "confidence": "inferred"}
    assert "SECRET PROSE" not in json.dumps(rendered)
