"""Purity check: composes write/call facts into a PurityResult.

Receiver-kind policy:

* IMPORT root + name in module denylist  → IMPURE
* PARAMETER root + I/O or mutation method → IMPURE (caller-visible)
* VARIABLE root + I/O method              → IMPURE (file/socket I/O is
                                              I/O regardless of whether
                                              the handle is local)
* VARIABLE root + collection method       → ignored (pure-local)
* SELF root                               → I/O always flagged;
                                              attribute mutations on self
                                              are flagged via the existing
                                              ATTRIBUTE_WRITE fact, not here
* UNKNOWN root + I/O method               → IMPURE (conservative)
* UNKNOWN root + collection method        → ignored (default to pure)
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from pypeeker.analysis.checks._purity_denylists import (
    ALL_TRACKED_METHOD_NAMES,
    IMPURE_BUILTINS,
    IO_METHOD_NAMES,
    MODULE_IMPURE_NAMES,
    TYPE_IMPURE_METHODS,
)
from pypeeker.analysis.context import AnalysisContext, ContextError
from pypeeker.analysis.facts import (
    AttributeMethodCall,
    AttributeWrite,
    ImpureBuiltinCall,
    ModuleCall,
    OuterScopeWrite,
    ReceiverKind,
    find_attribute_method_calls,
    find_attribute_writes,
    find_impure_builtin_calls,
    find_module_calls,
    find_outer_scope_writes,
)
from pypeeker.models.capabilities import Confidence
from pypeeker.storage.store import IndexStore


class PurityVerdict(str, Enum):
    IMPURE = "impure"
    PROBABLY_PURE = "probably_pure"
    UNKNOWN = "unknown"


class EvidenceKind(str, Enum):
    WRITES_OUTER_SCOPE = "writes_outer_scope"
    ATTRIBUTE_WRITE = "attribute_write"
    CALLS_IMPURE_BUILTIN = "calls_impure_builtin"
    CALLS_IMPURE_MODULE = "calls_impure_module"
    CALLS_IMPURE_METHOD = "calls_impure_method"
    TRANSITIVE_IMPURE_CALL = "transitive_impure_call"
    NOT_FOUND = "not_found"
    NOT_A_FUNCTION = "not_a_function"


class Evidence(BaseModel):
    kind: EvidenceKind
    line: int | None = None
    target: str | None = None
    detail: str | None = None


class PurityResult(BaseModel):
    symbol_id: str
    verdict: PurityVerdict
    confidence: Confidence
    evidence: list[Evidence] = []


# Methods to consider for AttributeMethodCall extraction. The fact extractor
# sees one combined denylist (IO + collection-mutation + every type-specific
# method). The check below applies the precise per-receiver-kind /
# per-receiver-type policy.
_ATTRIBUTE_DENYLIST: frozenset[str] = ALL_TRACKED_METHOD_NAMES


def check_purity(store: IndexStore, symbol_id: str) -> PurityResult:
    """Analyze a function for evidence of impurity."""
    ctx = AnalysisContext.for_function(store, symbol_id)
    if isinstance(ctx, ContextError):
        return _unknown_result(ctx)

    evidence: list[Evidence] = []
    evidence.extend(_to_evidence(f) for f in find_outer_scope_writes(ctx))
    evidence.extend(_to_evidence(f) for f in find_attribute_writes(ctx))
    evidence.extend(
        _to_evidence(f) for f in find_impure_builtin_calls(ctx, IMPURE_BUILTINS)
    )
    evidence.extend(
        _to_evidence(f) for f in find_module_calls(ctx, MODULE_IMPURE_NAMES)
    )
    for call in find_attribute_method_calls(ctx, _ATTRIBUTE_DENYLIST):
        keep = _keep_attribute_call(call)
        if keep:
            evidence.append(_to_evidence(call))

    verdict = (
        PurityVerdict.IMPURE if evidence else PurityVerdict.PROBABLY_PURE
    )
    return PurityResult(
        symbol_id=ctx.function_symbol.symbol_id,
        verdict=verdict,
        confidence=Confidence.HEURISTIC,
        evidence=evidence,
    )


def _keep_attribute_call(call: AttributeMethodCall) -> bool:
    """Decide whether an attribute method call counts as impurity.

    Type-aware path takes priority: when the receiver root has a known
    type annotation that's in our type denylist, we match the leaf
    against that type's exact method set. Falls back to the generic
    receiver-kind dispatch when no type info is available.
    """
    if call.receiver_type and call.receiver_type in TYPE_IMPURE_METHODS:
        return call.method in TYPE_IMPURE_METHODS[call.receiver_type]

    is_io = call.method in IO_METHOD_NAMES
    if call.receiver_kind == ReceiverKind.PARAMETER:
        # Mutating a parameter is caller-visible — flag everything.
        return True
    if call.receiver_kind == ReceiverKind.SELF:
        return is_io  # mutations on self.x are caught by attribute_write
    if call.receiver_kind == ReceiverKind.VARIABLE:
        # Local variable: I/O is still I/O, but collection mutations are pure-local.
        return is_io
    if call.receiver_kind == ReceiverKind.UNKNOWN:
        return is_io  # conservative on dynamic receivers
    return False


def check_purity_transitive(store: IndexStore, symbol_id: str) -> PurityResult:
    """Like :func:`check_purity` but follows project-internal CALL edges.

    A function pure in its own body but calling another impure function is
    flagged IMPURE with TRANSITIVE_IMPURE_CALL evidence pointing at the
    immediate impure callee. Builds the full call graph once and runs a
    fixpoint propagation, so this is more expensive than ``check_purity``
    — use only when transitive analysis is wanted.

    See :mod:`pypeeker.analysis.call_graph` for the limitations of the
    underlying graph (only resolved CALL edges; method calls on instance
    fields are invisible without type info).
    """
    from pypeeker.analysis.call_graph import build_call_graph, reachable_functions

    graph = build_call_graph(store)
    reachable = reachable_functions(graph, symbol_id)

    local_results: dict[str, PurityResult] = {
        sid: check_purity(store, sid) for sid in reachable
    }

    impure: set[str] = {
        sid for sid, r in local_results.items() if r.verdict == PurityVerdict.IMPURE
    }
    transitive_callees: dict[str, set[str]] = {}

    changed = True
    while changed:
        changed = False
        for caller in reachable:
            if caller in impure:
                continue
            for callee in graph.get(caller, frozenset()):
                if callee in impure:
                    impure.add(caller)
                    transitive_callees.setdefault(caller, set()).add(callee)
                    changed = True
                    break

    base = local_results.get(symbol_id)
    if base is None:
        # symbol_id wasn't resolvable as a function — preserve check_purity's
        # UNKNOWN result (we can compute it directly).
        return check_purity(store, symbol_id)

    if symbol_id not in impure:
        return base

    extra = sorted(transitive_callees.get(symbol_id, set()))
    if base.verdict == PurityVerdict.IMPURE:
        # Already impure directly; append the transitive callees as
        # additional evidence.
        return base.model_copy(update={
            "evidence": list(base.evidence) + [
                Evidence(kind=EvidenceKind.TRANSITIVE_IMPURE_CALL, target=t)
                for t in extra
            ],
        })

    # Was PROBABLY_PURE locally; transitive propagation found impurity.
    evidence = [
        Evidence(kind=EvidenceKind.TRANSITIVE_IMPURE_CALL, target=t)
        for t in extra
    ]
    return PurityResult(
        symbol_id=base.symbol_id,
        verdict=PurityVerdict.IMPURE,
        confidence=Confidence.HEURISTIC,
        evidence=evidence,
    )


class PurityChecker:
    """Stateful entry point that caches the underlying store/engine."""

    def __init__(self, store: IndexStore) -> None:
        self._store = store

    def check(self, symbol_id: str) -> PurityResult:
        return check_purity(self._store, symbol_id)

    def check_with_call_graph(self, symbol_id: str) -> PurityResult:
        """Run :func:`check_purity_transitive` over this checker's store."""
        return check_purity_transitive(self._store, symbol_id)


def _unknown_result(err: ContextError) -> PurityResult:
    kind = (
        EvidenceKind.NOT_FOUND
        if err.reason == "not_found"
        else EvidenceKind.NOT_A_FUNCTION
    )
    return PurityResult(
        symbol_id=err.symbol_id,
        verdict=PurityVerdict.UNKNOWN,
        confidence=Confidence.HEURISTIC,
        evidence=[Evidence(kind=kind, detail=err.detail)],
    )


def _to_evidence(fact: object) -> Evidence:
    """Map a typed fact to a purity Evidence record."""
    if isinstance(fact, OuterScopeWrite):
        return Evidence(
            kind=EvidenceKind.WRITES_OUTER_SCOPE,
            line=fact.line,
            target=fact.target_symbol_id,
        )
    if isinstance(fact, AttributeWrite):
        return Evidence(
            kind=EvidenceKind.ATTRIBUTE_WRITE,
            line=fact.line,
            target=fact.target,
        )
    if isinstance(fact, ImpureBuiltinCall):
        return Evidence(
            kind=EvidenceKind.CALLS_IMPURE_BUILTIN,
            line=fact.line,
            target=fact.name,
        )
    if isinstance(fact, ModuleCall):
        return Evidence(
            kind=EvidenceKind.CALLS_IMPURE_MODULE,
            line=fact.line,
            target=fact.full_name,
        )
    if isinstance(fact, AttributeMethodCall):
        return Evidence(
            kind=EvidenceKind.CALLS_IMPURE_METHOD,
            line=fact.line,
            target=fact.method,
            detail=fact.receiver_kind.value,
        )
    raise TypeError(f"Unsupported fact type: {type(fact).__name__}")
