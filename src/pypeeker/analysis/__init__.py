"""Semantic analysis layer: facts (atoms) + checks (composite verdicts)."""

from pypeeker.analysis.checks import (
    Evidence,
    EvidenceKind,
    PurityChecker,
    PurityResult,
    PurityVerdict,
    check_purity,
    check_purity_transitive,
)
from pypeeker.analysis.context import AnalysisContext, ContextError

__all__ = [
    "AnalysisContext",
    "ContextError",
    "Evidence",
    "EvidenceKind",
    "PurityChecker",
    "PurityResult",
    "PurityVerdict",
    "check_purity",
    "check_purity_transitive",
]
