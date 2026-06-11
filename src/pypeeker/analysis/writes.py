"""Queries about what a function writes.

Each function returns an :class:`Observations` of typed facts. The
observations are direct, untyped-by-policy reports of what the indexed
code does. Compositions like :mod:`pypeeker.analysis.purity` decide what
those observations *mean*.
"""

from __future__ import annotations

from dataclasses import dataclass

from pypeeker.analysis.calls import ReceiverKind, classify_receiver
from pypeeker.analysis.context import AnalysisContext
from pypeeker.analysis.observations import Observations
from pypeeker.models.references import ReferenceKind
from pypeeker.models.symbol_id import is_unresolved_attr, leaf_name


@dataclass(frozen=True)
class OuterScopeWrite:
    """The function writes to a symbol resolved outside its scope subtree.

    Captures global / nonlocal mutations after the binder's redirect.
    """

    line: int
    target: str
    """``symbol_id`` of the written symbol."""


@dataclass(frozen=True)
class AttributeWrite:
    """The function writes to an attribute (e.g. ``self.x = y``).

    ``receiver_kind`` classifies the receiver so the purity layer can decide
    meaning: writing to ``self`` / a local is pure-local, writing to a parameter
    or imported module is a caller-visible / global mutation.
    """

    line: int
    attribute: str
    """Leaf attribute name (e.g. ``"value"`` for ``self.value = x``)."""
    receiver_kind: ReceiverKind


def outer_scope_writes(ctx: AnalysisContext) -> Observations[OuterScopeWrite]:
    """Writes whose target symbol resolves outside the function's scope.

    Catches global/nonlocal mutations: the binder rewrites those WRITE
    references to point at the outer-scope symbol, so they fall into this
    bucket without explicit declaration tracking.
    """
    found: list[OuterScopeWrite] = []
    for ref in ctx.file_index.references:
        if ref.kind != ReferenceKind.WRITE:
            continue
        if ref.in_scope_id not in ctx.subtree:
            continue
        if ref.is_attribute_access:
            continue  # attribute writes are reported by attribute_writes
        if ref.symbol_id in ctx.local_symbol_ids:
            continue
        if is_unresolved_attr(ref.symbol_id):
            continue
        found.append(
            OuterScopeWrite(line=ref.location.span.start.line, target=ref.symbol_id)
        )
    return Observations(tuple(found))


def attribute_writes(ctx: AnalysisContext) -> Observations[AttributeWrite]:
    """Writes to attributes (``self.x = y``, ``obj.attr = z``).

    Identified by ``is_attribute_access``, whether or not the attribute resolves
    to a known member — writing through any attribute is a caller-visible
    mutation.
    """
    symbols_by_id = {s.symbol_id: s for s in ctx.file_index.symbols}
    found: list[AttributeWrite] = []
    for ref in ctx.file_index.references:
        if ref.kind != ReferenceKind.WRITE or not ref.is_attribute_access:
            continue
        if ref.in_scope_id not in ctx.subtree:
            continue
        found.append(
            AttributeWrite(
                line=ref.location.span.start.line,
                attribute=leaf_name(ref.symbol_id),
                receiver_kind=classify_receiver(ref, symbols_by_id),
            )
        )
    return Observations(tuple(found))
