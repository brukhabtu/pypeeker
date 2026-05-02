"""Fact extractors for call-related observations.

The receiver-chain metadata on attribute references (added by the binder)
makes these extractors structural rather than heuristic: we know exactly
who the receiver is and whether the chain resolves to a module, a
parameter, a local variable, or something dynamic.
"""

from __future__ import annotations

from collections.abc import Container

from pypeeker.analysis.context import AnalysisContext
from pypeeker.analysis.facts.models import (
    AttributeMethodCall,
    ImpureBuiltinCall,
    ModuleCall,
    ReceiverKind,
)
from pypeeker.models.references import Reference, ReferenceKind
from pypeeker.models.symbols import Symbol, SymbolKind

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


def find_module_calls(
    ctx: AnalysisContext,
    denylist: Container[str],
) -> list[ModuleCall]:
    """Calls of the form ``<module>.<...>.<method>`` whose full qualified
    name is in the denylist.

    Receiver root must resolve to an IMPORT symbol; we then build the full
    name as ``imported_from + chain[1:] + leaf`` (using ``imported_from``
    rather than the local name catches aliased imports like
    ``import os as o``).
    """
    facts: list[ModuleCall] = []
    symbols_by_id = _symbols_by_id(ctx)
    for ref in ctx.file_index.references:
        if ref.kind != ReferenceKind.CALL:
            continue
        if ref.in_scope_id not in ctx.subtree:
            continue
        if ref.receiver_root_symbol_id is None or ref.receiver_chain is None:
            continue
        root = symbols_by_id.get(ref.receiver_root_symbol_id)
        if root is None or root.kind != SymbolKind.IMPORT:
            continue
        if not root.imported_from:
            continue
        leaf = _leaf_method(ref)
        if leaf is None:
            continue
        full_name = ".".join([root.imported_from, *ref.receiver_chain[1:], leaf])
        if full_name not in denylist:
            continue
        facts.append(
            ModuleCall(full_name=full_name, line=ref.location.span.start.line)
        )
    return facts


def find_attribute_method_calls(
    ctx: AnalysisContext,
    denylist: Container[str],
) -> list[AttributeMethodCall]:
    """Method calls of the form ``<unresolved>.<method>`` matching the denylist.

    Each fact is annotated with the receiver_kind derived from the receiver
    root's symbol kind. Module-rooted calls are excluded here (covered by
    :func:`find_module_calls`).
    """
    facts: list[AttributeMethodCall] = []
    symbols_by_id = _symbols_by_id(ctx)
    for ref in ctx.file_index.references:
        if ref.kind != ReferenceKind.CALL:
            continue
        if ref.in_scope_id not in ctx.subtree:
            continue
        leaf = _leaf_method(ref)
        if leaf is None:
            continue
        if leaf not in denylist:
            continue
        receiver_kind = _classify_receiver(ref, symbols_by_id)
        if receiver_kind == ReceiverKind.IMPORT:
            # Module calls go through find_module_calls.
            continue
        receiver_type = ctx.local_type_names.get(ref.receiver_root_symbol_id) \
            if ref.receiver_root_symbol_id else None
        facts.append(
            AttributeMethodCall(
                method=leaf,
                line=ref.location.span.start.line,
                receiver_kind=receiver_kind,
                receiver_type=receiver_type,
            )
        )
    return facts


def _leaf_method(ref: Reference) -> str | None:
    """Return the leaf method name for an attribute call, or None."""
    sid = ref.symbol_id
    if sid.startswith(UNRESOLVED_PREFIX):
        return sid[len(UNRESOLVED_PREFIX):]
    if ref.is_attribute_access:
        # Resolved attribute (e.g. self.method) — pull leaf from the symbol_id tail.
        if "." in sid:
            return sid.rsplit(".", 1)[-1]
        if ":" in sid:
            return sid.rsplit(":", 1)[-1]
    return None


def _classify_receiver(
    ref: Reference, symbols_by_id: dict[str, Symbol]
) -> ReceiverKind:
    if ref.receiver_root_symbol_id is None:
        return ReceiverKind.UNKNOWN
    root = symbols_by_id.get(ref.receiver_root_symbol_id)
    if root is None:
        return ReceiverKind.UNKNOWN
    if root.kind == SymbolKind.IMPORT:
        return ReceiverKind.IMPORT
    if root.kind == SymbolKind.PARAMETER:
        # ``self`` and ``cls`` are formally parameters but treated separately
        # because mutating self is conventionally OK in __init__ etc. — leave
        # that policy decision to the check layer.
        if root.name in ("self", "cls"):
            return ReceiverKind.SELF
        return ReceiverKind.PARAMETER
    if root.kind == SymbolKind.VARIABLE:
        return ReceiverKind.VARIABLE
    return ReceiverKind.UNKNOWN


def _symbols_by_id(ctx: AnalysisContext) -> dict[str, Symbol]:
    return {s.symbol_id: s for s in ctx.file_index.symbols}
