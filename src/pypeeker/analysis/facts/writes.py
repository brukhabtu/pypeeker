"""Fact extractors for write-related observations."""

from __future__ import annotations

from pypeeker.analysis.context import AnalysisContext
from pypeeker.analysis.facts.models import AttributeWrite, OuterScopeWrite
from pypeeker.models.references import ReferenceKind

UNRESOLVED_PREFIX = "<unresolved>."


def find_outer_scope_writes(ctx: AnalysisContext) -> list[OuterScopeWrite]:
    """Writes whose target symbol resolves outside the function's scope subtree.

    Catches global/nonlocal mutations: the binder rewrites those WRITE
    references to point at the outer-scope symbol, so they fall into this
    bucket without explicit declaration tracking.
    """
    facts: list[OuterScopeWrite] = []
    for ref in ctx.file_index.references:
        if ref.kind != ReferenceKind.WRITE:
            continue
        if ref.in_scope_id not in ctx.subtree:
            continue
        if ref.symbol_id in ctx.local_symbol_ids:
            continue
        if ref.symbol_id.startswith(UNRESOLVED_PREFIX):
            continue
        facts.append(
            OuterScopeWrite(
                target_symbol_id=ref.symbol_id,
                line=ref.location.span.start.line,
            )
        )
    return facts


def find_attribute_writes(ctx: AnalysisContext) -> list[AttributeWrite]:
    """Writes to attributes (``self.x = y``, ``obj.attr = z``).

    Pypeeker stores these as WRITE references on a ``<unresolved>.<name>``
    target because the receiver chain is not preserved.
    """
    facts: list[AttributeWrite] = []
    for ref in ctx.file_index.references:
        if ref.kind != ReferenceKind.WRITE:
            continue
        if ref.in_scope_id not in ctx.subtree:
            continue
        if not ref.symbol_id.startswith(UNRESOLVED_PREFIX):
            continue
        facts.append(
            AttributeWrite(
                target=ref.symbol_id,
                line=ref.location.span.start.line,
            )
        )
    return facts
