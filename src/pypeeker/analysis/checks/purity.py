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
    COLLECTION_MUTATION_NAMES,
    IMPURE_BUILTINS,
    IO_METHOD_NAMES,
    MODULE_IMPURE_NAMES,
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


# Methods to consider for AttributeMethodCall extraction. Combined here so the
# fact extractor sees one denylist; per-method policy is applied below.
_ATTRIBUTE_DENYLIST: frozenset[str] = IO_METHOD_NAMES | COLLECTION_MUTATION_NAMES


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
    """Decide whether an attribute method call counts as impurity."""
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


class PurityChecker:
    """Stateful entry point that caches the underlying store/engine."""

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
