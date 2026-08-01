"""The ``variable-mutation`` trait: is a VARIABLE symbol written to, mutated
in place, or does a read of it escape.

Extracted from ``check.rules.prefer_tuple``'s unsafe-set logic (WRITE refs,
escaping READs, mutator method calls via receiver chains) so that analysis
exists in exactly one place. Two consumers now quantify the same trait
differently:

* :func:`pypeeker.check.rules.prefer_tuple` — a rule, ∀ over candidate
  ``VARIABLE`` symbols: find every one that is neither mutated nor escaping.
* :class:`pypeeker.refactor.preconditions.NotReassigned` — a precondition,
  pointwise on one resolved symbol: verify it before an inline-variable plan
  commits.

The two consumers do not want quite the same boolean, which is why
:class:`VariableMutation` keeps ``has_write_ref`` and ``mutator_call``
separate instead of folding them into one ``is_mutated`` up front — see the
class docstring's "one-vs-two-values" note.
"""

from __future__ import annotations

from dataclasses import dataclass

from pypeeker.analysis.traits import Trait, register_trait
from pypeeker.models import Confidence, FileIndex, ReferenceKind

VARIABLE_MUTATION = "variable-mutation"

# Methods that mutate a list in place. A list-literal local touched by none of
# these (and never subscript-written) is a tuple candidate for prefer-tuple.
_LIST_MUTATORS: frozenset[str] = frozenset({
    "append", "extend", "insert", "remove", "pop", "clear", "sort", "reverse",
})


@dataclass(frozen=True)
class VariableMutation:
    """Mutation/escape facts about one VARIABLE symbol's references.

    ``has_write_ref`` — a WRITE reference exists for this exact
    ``symbol_id``: a subscript write (``x[i] = v``) or augmented assignment
    (``x += v``) — a destructive rewrite of the binding's contents,
    independent of what its reads look like.

    ``mutator_call`` — a list-mutating method (append/extend/insert/remove/
    pop/clear/sort/reverse) was called on this symbol as its sole,
    unqualified receiver (``x.append(v)``, not ``x.y.append(v)``).

    ``escaping_read`` — a READ reference lets the value escape its local,
    read-only lifetime — returned, yielded, passed as a call argument,
    aliased, concatenated, compared, or otherwise used where a tuple would
    behave differently (:attr:`~pypeeker.models.references.Reference.escapes`,
    set ``False`` by the binder only in provably tuple-equivalent positions).

    **One-vs-two-values judgment call.** ``has_write_ref`` and
    ``mutator_call`` are kept as two separate booleans rather than one
    ``is_mutated`` field computed here, even though ``prefer_tuple`` only
    ever wants their union. The reason is ``NotReassigned``: it wants
    ``has_write_ref`` *alone* — a ``.append()`` call does not "reassign" a
    binding the way ``inline-variable`` means it (the value inlined is
    whatever the RHS evaluated to; later in-place mutation of a list doesn't
    change which expression the *name* was bound to), so unioning it into a
    combined ``is_mutated`` and having ``NotReassigned`` read that would
    silently change its refusal behavior. Forcing a false unification here
    would either break ``NotReassigned``'s byte-identical parity or smuggle
    a behavior change into a "no-op" migration; splitting the fact honestly
    avoids both. ``is_mutated`` is offered below as a convenience property
    for the one consumer (``prefer_tuple``) that wants the union.
    """

    has_write_ref: bool
    mutator_call: bool
    escaping_read: bool

    @property
    def is_mutated(self) -> bool:
        """True if the binding is rewritten in place: a WRITE ref or a mutator call."""
        return self.has_write_ref or self.mutator_call


@register_trait(VARIABLE_MUTATION)
def _variable_mutation(file_index: FileIndex, symbol_id: str) -> Trait:
    """Derive :class:`VariableMutation` for ``symbol_id`` from ``file_index``'s references.

    ``DECLARED`` confidence: every fact read here (a reference's ``kind``,
    ``escapes``, or attribute-call shape) comes straight from what the
    binder recorded as written in source — no inference step is involved.
    """
    has_write_ref = False
    mutator_call = False
    escaping_read = False
    # Deliberate non-goal of TASK-134's lookup work: this reference scan stays
    # linear. Indexing it needs two keys (``symbol_id`` and
    # ``receiver_root_symbol_id``) plus faithful reproduction of the if/elif
    # exclusion below — a ref matching ``symbol_id`` never reaches the receiver
    # branch — and this trait gates ``NotReassigned``'s refusal, so the blast
    # radius was kept on the ``type-annotation`` provider (the one
    # ``prefer_tuple`` pays per candidate). See
    # analysis/type_annotation.py::_symbols_by_id.
    for ref in file_index.references:
        if ref.symbol_id == symbol_id:
            if ref.kind == ReferenceKind.WRITE:
                has_write_ref = True
            elif ref.kind == ReferenceKind.READ and ref.escapes:
                escaping_read = True
        elif (
            ref.kind == ReferenceKind.CALL
            and ref.is_attribute_access
            and ref.receiver_root_symbol_id == symbol_id
            and ref.receiver_chain is not None
            and len(ref.receiver_chain) == 1
            and ref.symbol_id.rsplit(".", 1)[-1] in _LIST_MUTATORS
        ):
            mutator_call = True

    value = VariableMutation(
        has_write_ref=has_write_ref,
        mutator_call=mutator_call,
        escaping_read=escaping_read,
    )
    return Trait(
        value=value,
        confidence=Confidence.DECLARED,
        provenance=(
            "pypeeker.analysis.variable_mutation: WRITE/CALL/READ references "
            f"to '{symbol_id}' in {file_index.file_path}"
        ),
    )
