"""Queries about what a function writes.

Each function returns an :class:`Observations` of typed facts. The
observations are direct, untyped-by-policy reports of what the indexed
code does. Compositions like :mod:`pypeeker.analysis.purity` decide what
those observations *mean*.
"""

from __future__ import annotations

from dataclasses import dataclass

from pypeeker.analysis.context import AnalysisContext
from pypeeker.analysis.observations import Observations
from pypeeker.models.references import ReferenceKind

UNRESOLVED_PREFIX = "<unresolved>."


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

    The receiver chain isn't preserved by pypeeker's binder; only the leaf
    attribute name is recorded.
    """

    line: int
    attribute: str
    """Leaf attribute name (e.g. ``"value"`` for ``self.value = x``)."""


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
        if ref.symbol_id in ctx.local_symbol_ids:
            continue
        if ref.symbol_id.startswith(UNRESOLVED_PREFIX):
            continue
        found.append(
            OuterScopeWrite(line=ref.location.span.start.line, target=ref.symbol_id)
        )
    return Observations(tuple(found))


def attribute_writes(ctx: AnalysisContext) -> Observations[AttributeWrite]:
    """Writes to attributes (``self.x = y``, ``obj.attr = z``).

    Pypeeker stores these as WRITE references on a ``<unresolved>.<name>``
    target because the receiver chain isn't preserved.
    """
    found: list[AttributeWrite] = []
    for ref in ctx.file_index.references:
        if ref.kind != ReferenceKind.WRITE:
            continue
        if ref.in_scope_id not in ctx.subtree:
            continue
        if not ref.symbol_id.startswith(UNRESOLVED_PREFIX):
            continue
        found.append(
            AttributeWrite(
                line=ref.location.span.start.line,
                attribute=ref.symbol_id[len(UNRESOLVED_PREFIX):],
            )
        )
    return Observations(tuple(found))
