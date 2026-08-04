"""The evidence lattice, and the derivation node that records how it was applied.

Fork #4 of ``dsl-rewrite.md``: *confidence composes by* **meet (min) over every
contribution** *on* ``DECLARED > INFERRED > HEURISTIC``, *order-independent,
with the stated law that reading* ``.confidence`` *is a DECLARED meta-read.*
This module is the whole of that resolution: :func:`meet` is the lattice
operation, and :class:`Derivation` is the node every evaluation produces so
``--why`` (fork #7) can serialize the tree without a second structure existing.

**Four levels, not three.** :class:`~pypeeker.models.Confidence` has a fourth
member, ``UNKNOWN``, which fork #4 does not name — because ``UNKNOWN`` carries
no evidence, so there is nothing for the ordering to say about it beyond
"worst". It is the **absorbing bottom**: ``meet(anything, UNKNOWN)`` is
``UNKNOWN``. This is not a divergence; it falls out of ranking the enum
honestly, and it is load-bearing on real data —
``pypeeker.analysis.type_annotation`` returns ``UNKNOWN`` for any symbol
without an annotation, so an implementation handling only three levels would
fail on the first such symbol it saw.

**Why the meet is order-independent.** It is commutative, associative and
idempotent, with identity ``DECLARED`` (the empty meet: an expression that
contributed no evidence claims nothing, so it cannot lower anything) and
absorbing element ``UNKNOWN``. A confidence is therefore a property of the
*set* of contributions, never of the sequence.

**Why that does not contradict written-order evaluation (fork #3).** Clauses
run in the order written, and a conjunction may stop at its first false
operand — observable through an opaque's side effects. It may do so only where
the answer being false *discards the row*, so the partial meet is never
reported. Where falsity is instead read — under ``not_()``, under
``.eq(False)`` — the conjunction runs every operand, because each one is then
a contribution to the meet. See :attr:`pypeeker.dsl.EvalContext.discards_falsity`.
So the evaluation *schedule* is written-order while the confidence *result* is
order-independent. The two forks constrain different things.

**The meta-read law, and why ``.at()`` is a leaf.** Reading a trait's value
inherits that trait's confidence; reading a trait's ``.confidence`` yields
``DECLARED``, because *what level the index recorded* is itself a declared
fact about the index. The consequence the divergence ledger pins down
(``dsl-rewrite.md``, "``prefer-tuple`` reports at DECLARED via the meta-read
law; a port that meets to INFERRED is wrong, not divergent") is reached by
making the assertion ``trait_of(T).at(INFERRED)`` a **primitive leaf** that
reports ``DECLARED`` — it read ``.confidence``, so the law applies to it
directly — rather than by inventing a rule that lets one operand of a
conjunction retroactively upgrade another. See
:class:`pypeeker.dsl.expr.TraitAssertion`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from pypeeker.models import Confidence

CONFIDENCE_RANK: Mapping[Confidence, int] = MappingProxyType({
    Confidence.UNKNOWN: 0,
    Confidence.HEURISTIC: 1,
    Confidence.INFERRED: 2,
    Confidence.DECLARED: 3,
})
"""Total order on the evidence lattice, bottom (``UNKNOWN``) to top (``DECLARED``).

The three levels fork #4 names occupy ranks 1-3 in the order it states;
``UNKNOWN`` sits below all of them as the absorbing bottom.
"""

_NO_DETAIL: Mapping[str, Any] = MappingProxyType({})


def meet(*levels: Confidence) -> Confidence:
    """Return the weakest of ``levels`` — the lattice meet.

    The empty meet is :attr:`~pypeeker.models.Confidence.DECLARED`, the
    lattice identity: a derivation with no evidence contributions makes no
    claim that could weaken anything. ``UNKNOWN`` absorbs.

    Commutative, associative and idempotent, so the result depends on the
    *set* of arguments and not their order — which is exactly what fork #4
    means by "order-independent".
    """
    weakest = Confidence.DECLARED
    for level in levels:
        if CONFIDENCE_RANK[level] < CONFIDENCE_RANK[weakest]:
            weakest = level
    return weakest


@dataclass(frozen=True)
class Derivation:
    """One node of the derivation tree: what an expression computed, and on what evidence.

    Every expression evaluation produces exactly one of these, with the nodes
    its sub-expressions produced hanging off :attr:`inputs`. There is no
    separate provenance structure — this tree *is* what ``--why`` serializes
    (see :mod:`pypeeker.dsl.provenance`), which is why the fields stay small
    and JSON-shaped.

    ``op`` — the node kind, e.g. ``"field"``, ``"trait.value"``, ``"all_of"``.
    ``value`` — what the node evaluated to.
    ``confidence`` — the evidence supporting ``value``, per :func:`meet`.
    ``inputs`` — child derivations, in written order.
    ``reads`` — the substrate this subtree touched, as prefixed tokens:
    ``field:<name>``, ``trait:<name>``, or an opaque's declared ``reads=``
    tokens verbatim. Load-bearing for opaques, whose bodies ``--why`` cannot
    see: the declaration is all the tree can report about them.
    ``detail`` — op-specific extras, flattened into the ``--why`` node.

    Deliberately **not** here: a :attr:`pypeeker.analysis.Trait.provenance`
    string. That field is unstructured debugging prose which
    :mod:`pypeeker.analysis.traits` forbids from ever reaching output; this
    tree is versioned structured data. The two are compatible only because
    they are different objects, so no provenance string is ever copied into a
    derivation.
    """

    op: str
    value: Any
    confidence: Confidence
    inputs: tuple[Derivation, ...] = ()
    reads: frozenset[str] = frozenset()
    detail: Mapping[str, Any] = field(default=_NO_DETAIL)
