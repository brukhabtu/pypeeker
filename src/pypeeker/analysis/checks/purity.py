"""Purity check: composes write/call facts into a PurityResult.

This check applies one purity-specific policy: an attribute method call
whose receiver is a local variable is *not* impure (mutating a local list
is fine). Mutating a parameter still counts.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from pypeeker.analysis.context import AnalysisContext, ContextError
from pypeeker.analysis.facts import (
    AttributeMethodCall,
    AttributeWrite,
    ImpureBuiltinCall,
    OuterScopeWrite,
    find_attribute_method_calls,
    find_attribute_writes,
    find_impure_builtin_calls,
    find_outer_scope_writes,
)
from pypeeker.analysis.checks._purity_denylists import (
    IMPURE_ATTRIBUTE_NAMES,
    IMPURE_BUILTINS,
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
    CALLS_IMPURE_STDLIB = "calls_impure_stdlib"
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
    for call in find_attribute_method_calls(ctx, IMPURE_ATTRIBUTE_NAMES):
        # Purity-specific policy: local-var mutation is not impure.
        if call.receiver_is_local_variable:
            continue
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


class PurityChecker:
    """Stateful entry point that caches the underlying store/engine.

    Equivalent to calling :func:`check_purity` directly, but lets callers
    perform many checks against the same store without rebuilding internal
    state.
    """

    def __init__(self, store: IndexStore) -> None:
        self._store = store

    def check(self, symbol_id: str) -> PurityResult:
        return check_purity(self._store, symbol_id)


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
    if isinstance(fact, AttributeMethodCall):
        return Evidence(
            kind=EvidenceKind.CALLS_IMPURE_STDLIB,
            line=fact.line,
            target=f"<unresolved>.{fact.method}",
            detail=fact.method,
        )
    raise TypeError(f"Unsupported fact type: {type(fact).__name__}")
