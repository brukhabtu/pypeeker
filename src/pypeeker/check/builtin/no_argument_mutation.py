"""Builtin check rule: ``no-argument-mutation`` (advisory, opt-in).

Flags functions that mutate their parameters — caller-visible side effects
that no syntactic linter sees. Three mutation shapes are detected, all built
on the binder's receiver-chain metadata:

* **collection-mutator calls** whose receiver root is a parameter
  (``items.append(x)``, ``cfg.options.update(d)``) — the mutator name table
  comes from :data:`pypeeker.analysis.purity.DEFAULT_POLICY`
  (``collection_mutation_names``), extensible via ``extra-mutators``;
* **attribute writes** on a parameter receiver (``obj.x = 1``);
* **subscript writes** whose root is a parameter (``xs[0] = 1`` — the binder
  records these as a WRITE reference on the root symbol).

``self`` / ``cls`` receivers are excluded (mutating your own instance is the
job of a method, not an argument mutation). Local-variable mutations are
pure-local and never flagged.

Options (``[tool.pypeeker.no-argument-mutation]``):
    ``allow``          — fnmatch patterns matched against the function's
                         ``symbol_id`` (``"pkg.mod:func"``); matching
                         functions are skipped entirely (e.g. functions
                         documented as in-place mutators).
    ``extra-mutators`` — method names added to the collection-mutator set
                         (e.g. ``["enqueue", "register"]``).

Known coarseness (advisory by design):

* augmented assignment to a parameter (``x += 1``) is recorded by the binder
  with the same WRITE shape as a subscript write, so it is flagged too — for
  mutable types that *is* caller-visible (``x += [1]``); for immutables it is
  shadowing worth a second look anyway;
* aliasing (``y = items; y.append(1)``) is invisible without escape analysis.

Opt-in: not enabled in any default rules list; enable it via
``[tool.pypeeker].rules``.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Container, Mapping
from typing import Any

from pypeeker.analysis.calls import ReceiverKind, classify_receiver
from pypeeker.analysis.context import AnalysisContext, ContextError
from pypeeker.analysis.purity import DEFAULT_POLICY
from pypeeker.check.context import CheckContext
from pypeeker.check.models import Violation
from pypeeker.check.rules import register_rule
from pypeeker.models.references import Reference, ReferenceKind
from pypeeker.models.symbol_id import (
    is_unresolved_attr,
    leaf_name,
    unresolved_attr_name,
)
from pypeeker.models.symbols import Symbol, SymbolKind
from pypeeker.query.engine import SemanticQueryEngine

NO_ARGUMENT_MUTATION = "no-argument-mutation"

_SELF_NAMES = ("self", "cls")


@register_rule(NO_ARGUMENT_MUTATION, scope="project")
def _no_argument_mutation(
    context: CheckContext, options: Mapping[str, Any]
) -> list[Violation]:
    """Flag parameter mutations in every FUNCTION / METHOD across the project.

    One violation per mutation site, located at the mutation (1-indexed
    line), naming the function, the parameter, and how it was mutated.
    """
    allow = _as_str_list(options.get("allow"))
    mutators: frozenset[str] = DEFAULT_POLICY.collection_mutation_names | frozenset(
        _as_str_list(options.get("extra-mutators"))
    )
    engine = SemanticQueryEngine(context.store)

    violations: list[Violation] = []
    for index in context.indexes:
        symbols_by_id = {s.symbol_id: s for s in index.symbols}
        for symbol in index.symbols:
            if symbol.kind not in (SymbolKind.FUNCTION, SymbolKind.METHOD):
                continue
            if any(
                fnmatch.fnmatchcase(symbol.symbol_id, pattern) for pattern in allow
            ):
                continue
            ctx = AnalysisContext.for_function(
                context.store, symbol.symbol_id, engine=engine
            )
            if isinstance(ctx, ContextError):
                continue
            for ref in ctx.file_index.references:
                if ref.in_scope_id not in ctx.subtree:
                    continue
                detail = _mutation_detail(ref, symbols_by_id, mutators)
                if detail is None:
                    continue
                violations.append(
                    Violation(
                        file_path=ref.location.file_path,
                        line=ref.location.span.start.line + 1,
                        rule=NO_ARGUMENT_MUTATION,
                        message=(
                            f"{symbol.kind.value} '{symbol.symbol_id}': {detail}"
                        ),
                    )
                )
    return violations


def _mutation_detail(
    ref: Reference,
    symbols_by_id: dict[str, Symbol],
    mutators: Container[str],
) -> str | None:
    """Describe the parameter mutation ``ref`` performs, or None.

    Covers the three detectable shapes: mutator method calls, attribute
    writes, and subscript writes. ``self``/``cls`` receivers never match —
    :func:`classify_receiver` maps them to :data:`ReceiverKind.SELF` for the
    attribute shapes, and the subscript shape checks the name explicitly.
    """
    if ref.kind == ReferenceKind.CALL and ref.is_attribute_access:
        method = _leaf_method(ref)
        if method is None or method not in mutators:
            return None
        if classify_receiver(ref, symbols_by_id) != ReceiverKind.PARAMETER:
            return None
        root = symbols_by_id[ref.receiver_root_symbol_id]  # PARAMETER ⇒ resolved
        chain = ref.receiver_chain or []
        via = ".".join([*chain[1:], method])
        return f"parameter '{root.name}' mutated via {via}()"

    if ref.kind == ReferenceKind.WRITE and ref.is_attribute_access:
        if classify_receiver(ref, symbols_by_id) != ReceiverKind.PARAMETER:
            return None
        root = symbols_by_id[ref.receiver_root_symbol_id]
        return (
            f"parameter '{root.name}' mutated via attribute write "
            f"'.{leaf_name(ref.symbol_id)}'"
        )

    if ref.kind == ReferenceKind.WRITE and not ref.is_attribute_access:
        target = symbols_by_id.get(ref.symbol_id)
        if target is None or target.kind != SymbolKind.PARAMETER:
            return None
        if target.name in _SELF_NAMES:
            return None
        return f"parameter '{target.name}' mutated via subscript write"

    return None


def _leaf_method(ref: Reference) -> str | None:
    """Leaf method name of an attribute call, or None for non-attribute refs.

    Mirrors the shape handling in :mod:`pypeeker.analysis.calls`: unresolved
    attribute chains carry the leaf in the sentinel id; resolved attribute
    calls carry it as the final ``.`` / ``:`` segment.
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


def _as_str_list(raw: Any) -> list[str]:
    """Coerce an option value to a list of strings ('' / None / [] -> [])."""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw else []
    return [str(value) for value in raw]
