"""Queries about what a function calls.

The receiver-chain metadata on attribute references (added by the binder)
makes these queries structural rather than heuristic: we know exactly who
the receiver is and whether the chain resolves to a module, a parameter,
a local variable, or something dynamic.
"""

from __future__ import annotations

from collections.abc import Container
from dataclasses import dataclass
from enum import Enum

from pypeeker.analysis.context import AnalysisContext
from pypeeker.analysis.observations import Observations
from pypeeker.models import (
    Reference,
    ReferenceKind,
    Symbol,
    SymbolKind,
    builtin_name,
    is_builtin,
    is_unresolved_attr,
    unresolved_attr_name,
)


class ReceiverKind(str, Enum):
    """How the receiver of an attribute call resolves.

    Drives downstream policy: a parameter mutation is caller-visible
    (impure), a local variable mutation is pure-local, an unknown receiver
    forces conservative classification.
    """

    IMPORT = "import"
    PARAMETER = "parameter"
    VARIABLE = "variable"
    SELF = "self"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BareCall:
    """A call to an unresolved bare name (e.g. ``print(x)``)."""

    line: int
    name: str


@dataclass(frozen=True)
class ModuleCall:
    """A fully-qualified call into an imported module.

    Computed when an attribute call's receiver root resolves to an IMPORT
    symbol — combines ``imported_from + chain[1:] + leaf`` into a canonical
    name like ``os.system`` or ``pathlib.Path.write_text``.
    """

    line: int
    qualified_name: str


@dataclass(frozen=True)
class AttributeMethodCall:
    """A method call on an attribute receiver.

    ``receiver_kind`` and ``receiver_type`` let downstream policy decide
    how to interpret this: parameter mutations are caller-visible; local
    variable mutations are pure-local; typed receivers (e.g. ``Path``) get
    type-specific treatment.
    """

    line: int
    method: str
    receiver_kind: ReceiverKind
    receiver_type: str | None = None


def bare_calls(
    ctx: AnalysisContext,
    denylist: Container[str],
) -> Observations[BareCall]:
    """Calls to bare builtin/unresolved names matching ``denylist``.

    Two reference shapes count as "bare":
      * ``symbol_id='<builtins>.print'`` (builtin, resolved by the binder)
      * ``symbol_id='print'`` (unresolved, e.g. names from star-imports or
        free variables we couldn't bind)

    Anything resolved to a project symbol (``file.py:Class.method``) is a
    different concern handled by call-graph analysis.
    """
    found: list[BareCall] = []
    for ref in ctx.file_index.references:
        if ref.kind != ReferenceKind.CALL:
            continue
        if ref.in_scope_id not in ctx.subtree:
            continue
        sid = ref.symbol_id
        if is_builtin(sid):
            name = builtin_name(sid)
        elif not ref.resolved and ":" not in sid and not is_unresolved_attr(sid):
            name = sid
        else:
            continue
        if name not in denylist:
            continue
        found.append(BareCall(line=ref.location.span.start.line, name=name))
    return Observations(tuple(found))


def module_calls(
    ctx: AnalysisContext,
    denylist: Container[str],
) -> Observations[ModuleCall]:
    """Calls of the form ``<module>.<...>.<method>`` whose full qualified
    name is in ``denylist``.

    Receiver root must resolve to an IMPORT symbol; we then build the full
    name as ``imported_from + chain[1:] + leaf`` (using ``imported_from``
    rather than the local name catches aliased imports like
    ``import os as o``).
    """
    found: list[ModuleCall] = []
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
        found.append(
            ModuleCall(line=ref.location.span.start.line, qualified_name=full_name)
        )
    return Observations(tuple(found))


def attribute_method_calls(
    ctx: AnalysisContext,
    denylist: Container[str],
) -> Observations[AttributeMethodCall]:
    """Method calls of the form ``<unresolved>.<method>`` matching ``denylist``.

    Each fact is annotated with the receiver_kind derived from the receiver
    root's symbol kind (IMPORT-rooted calls are excluded — covered by
    :func:`module_calls`) and receiver_type when the root has a normalized
    type annotation.
    """
    found: list[AttributeMethodCall] = []
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
        receiver_kind = classify_receiver(ref, symbols_by_id)
        if receiver_kind == ReceiverKind.IMPORT:
            continue
        receiver_type = (
            ctx.local_type_names.get(ref.receiver_root_symbol_id)
            if ref.receiver_root_symbol_id
            else None
        )
        found.append(
            AttributeMethodCall(
                line=ref.location.span.start.line,
                method=leaf,
                receiver_kind=receiver_kind,
                receiver_type=receiver_type,
            )
        )
    return Observations(tuple(found))


def _leaf_method(ref: Reference) -> str | None:
    """Return the leaf method name for an attribute call, or None.

    Unlike :func:`pypeeker.models.symbol_id.leaf_name` (which always returns
    a name), this returns None for references that are not attribute access —
    only attribute calls have a "method" leaf.
    """
    sid = ref.symbol_id
    if is_unresolved_attr(sid):
        return unresolved_attr_name(sid)
    if ref.is_attribute_access:
        if "." in sid:
            return sid.rsplit(".", 1)[-1]
        if ":" in sid:
            return sid.rsplit(":", 1)[-1]
    return None


def classify_receiver(
    ref: Reference, symbols_by_id: dict[str, Symbol]
) -> ReceiverKind:
    """Classify an attribute reference's receiver root (self/param/var/import).

    Drives purity policy: ``self``/``cls`` and local-variable mutations are
    pure-local, while parameter and imported-module receivers are externally
    visible.
    """
    if ref.receiver_root_symbol_id is None:
        return ReceiverKind.UNKNOWN
    root = symbols_by_id.get(ref.receiver_root_symbol_id)
    if root is None:
        return ReceiverKind.UNKNOWN
    if root.kind == SymbolKind.IMPORT:
        return ReceiverKind.IMPORT
    if root.kind == SymbolKind.PARAMETER:
        if root.name in ("self", "cls"):
            return ReceiverKind.SELF
        return ReceiverKind.PARAMETER
    if root.kind == SymbolKind.VARIABLE:
        return ReceiverKind.VARIABLE
    return ReceiverKind.UNKNOWN


def _symbols_by_id(ctx: AnalysisContext) -> dict[str, Symbol]:
    return {s.symbol_id: s for s in ctx.file_index.symbols}
