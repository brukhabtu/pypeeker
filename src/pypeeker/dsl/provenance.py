"""``--why``: the derivation tree as versioned, additive-only JSON.

Fork #7 of ``dsl-rewrite.md``: *provenance is exposed —* ``--why`` *returns the
derivation tree as versioned, additive-only JSON*, because the product thesis
is LLM consumers and an agent cannot attach a debugger. This module is the
serializer, and nothing more: the tree it renders is the
:class:`~pypeeker.dsl.Derivation` graph every evaluation already built, so
there is no second structure to keep in sync.

**Versioned at the tree's root, not the command's.** :data:`PROVENANCE_SCHEMA`
belongs to the derivation document — that is the thing fork #7 versions — so
:func:`derivation_document` stamps it and the CLI nests the result under a
``why`` key. Minting a second, differently-shaped ``schema`` at the top of a
command envelope would collide with the ``{"schema": 1, "findings": [...]}``
contract ``scripts/parity-manifest.toml`` already reserves for the new engine,
and CLI envelopes are frozen except for additions.

**Additive-only, enforced by how it is tested.** Consumers must read this by
key lookup and ignore keys they do not know; the schema tests assert the
required keys are a *subset* of each node's keys, never an equality, so adding
a key later cannot break them. The version increments only when an existing
key's meaning changes or a key disappears.

**The guardrail this must not violate.** :attr:`pypeeker.analysis.Trait`'s
``provenance`` field is unstructured debugging prose that
:mod:`pypeeker.analysis.traits` forbids from ever reaching output —
``tests/test_traits.py`` pins it. Fork #7 requires ``--why`` *in* output. The
two are compatible because they are different objects: one is free prose with
no schema, the other is versioned structured data built from op names, field
names, trait names and confidence levels. The DSL never copies a ``Trait``
into a derivation in the first place — :class:`~pypeeker.dsl.TraitRead` lifts
out ``value`` or ``confidence`` and drops the rest — and :func:`_jsonable`
below is the second line of defence, projecting any ``Trait`` that somehow
reaches it down to those same two members.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import Enum
from typing import Any

from pypeeker.analysis import Trait
from pypeeker.dsl.evidence import Derivation
from pypeeker.dsl.expr import UNMATCHED

PROVENANCE_SCHEMA = 1
"""Version of the derivation-document shape. Additive changes do not bump it."""

UNMATCHED_JSON = "<unmatched>"
"""How :data:`~pypeeker.dsl.UNMATCHED` renders in JSON.

An assertion whose level did not hold has no value, and ``null`` is already
taken — ``type-annotation`` yields ``None`` for an unannotated symbol — so the
sentinel keeps its own rendering rather than collapsing into a legitimate one.
"""

def _jsonable(value: Any) -> Any:
    """Coerce ``value`` into something :func:`json.dumps` accepts without a default.

    Args:
        value: any Python value appearing in a derivation node.

    Returns:
        A JSON-safe projection. Enums render as their value, sets sort,
        :class:`~pypeeker.analysis.Trait` loses its provenance prose, and
        anything else falls back to ``repr`` rather than failing to serialize.
    """
    if value is UNMATCHED:
        return UNMATCHED_JSON
    if isinstance(value, Trait):
        # Defence in depth: the grammar cannot put a Trait here, and if a future
        # node did, its provenance prose must still not reach output.
        return {"value": _jsonable(value.value), "confidence": _jsonable(value.confidence)}
    if isinstance(value, Enum):
        return _jsonable(value.value)
    if value is None or isinstance(value, bool | str | int | float):
        return value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, frozenset | set):
        return sorted(_jsonable(item) for item in value)
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    return repr(value)


def derivation_to_dict(node: Derivation) -> dict[str, Any]:
    """Serialize one derivation node and its inputs.

    Args:
        node: the node to render, with its children in :attr:`Derivation.inputs`.

    Returns:
        ``{"op", "value", "confidence", "reads", "inputs"}`` plus the node's
        op-specific ``detail`` keys flattened alongside them — ``field`` and
        ``present`` for a field read, ``trait``/``level``/``matched`` for an
        assertion, ``declared_reads`` for an opaque whose body cannot be shown.
        The five core keys are written last so a future ``detail`` key can
        never shadow one.
    """
    rendered: dict[str, Any] = {key: _jsonable(item) for key, item in node.detail.items()}
    rendered["op"] = node.op
    rendered["value"] = _jsonable(node.value)
    rendered["confidence"] = _jsonable(node.confidence)
    rendered["reads"] = sorted(node.reads)
    rendered["inputs"] = [derivation_to_dict(child) for child in node.inputs]
    return rendered


def derivation_document(derivations: Iterable[Derivation]) -> dict[str, Any]:
    """Wrap derivation trees into the versioned document ``--why`` emits.

    Args:
        derivations: one tree per filter stage the row survived, in written
            order. Plural because a selection may filter more than once, and
            collapsing the trees into one would invent a conjunction nobody
            wrote.

    Returns:
        ``{"schema": PROVENANCE_SCHEMA, "derivations": [...]}``.
    """
    return {
        "schema": PROVENANCE_SCHEMA,
        "derivations": [derivation_to_dict(node) for node in derivations],
    }
