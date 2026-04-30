"""Fact extractors for call-related observations."""

from __future__ import annotations

from collections.abc import Container

from pypeeker.analysis.context import AnalysisContext
from pypeeker.analysis.facts.models import AttributeMethodCall, ImpureBuiltinCall
from pypeeker.models.references import ReferenceKind

UNRESOLVED_PREFIX = "<unresolved>."


def find_impure_builtin_calls(
    ctx: AnalysisContext,
    denylist: Container[str],
) -> list[ImpureBuiltinCall]:
    """Calls to bare unresolved names matching the denylist (builtins).

    Bare unresolved names are stored as ``symbol_id='print'`` (no colon, no
    prefix). Anything resolved to a project symbol is a different concern
    (call-graph analysis).
    """
    facts: list[ImpureBuiltinCall] = []
    for ref in ctx.file_index.references:
        if ref.kind != ReferenceKind.CALL:
            continue
        if ref.in_scope_id not in ctx.subtree:
            continue
        sid = ref.symbol_id
        if ref.resolved or ":" in sid or sid.startswith(UNRESOLVED_PREFIX):
            continue
        if sid not in denylist:
            continue
        facts.append(
            ImpureBuiltinCall(name=sid, line=ref.location.span.start.line)
        )
    return facts


def find_attribute_method_calls(
    ctx: AnalysisContext,
    denylist: Container[str],
) -> list[AttributeMethodCall]:
    """Calls of the form ``<unresolved>.<method>`` whose method name is on the denylist.

    The receiver's identity is not preserved by the binder, so we report
    whether *some* same-line read targets a local variable (best heuristic
    for "the receiver is probably a local"). Checks decide what to do with
    that flag.
    """
    facts: list[AttributeMethodCall] = []
    for ref in ctx.file_index.references:
        if ref.kind != ReferenceKind.CALL:
            continue
        if ref.in_scope_id not in ctx.subtree:
            continue
        sid = ref.symbol_id
        if not sid.startswith(UNRESOLVED_PREFIX):
            continue
        method = sid[len(UNRESOLVED_PREFIX):]
        if method not in denylist:
            continue
        line = ref.location.span.start.line
        same_line_reads = ctx.reads_by_line.get(line, frozenset())
        receiver_is_local = bool(same_line_reads & ctx.local_variable_ids)
        facts.append(
            AttributeMethodCall(
                method=method,
                line=line,
                receiver_is_local_variable=receiver_is_local,
            )
        )
    return facts
