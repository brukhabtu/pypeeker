"""Composite checks that compose facts into verdicts.

Each check applies its own policy when consuming facts. Future siblings:
``determinism.py``, ``side_effects.py``, ``thread_safety.py``.
"""

from pypeeker.analysis.checks.purity import (
    Evidence,
    EvidenceKind,
    PurityChecker,
    PurityResult,
    PurityVerdict,
    check_purity,
    check_purity_transitive,
)

__all__ = [
    "Evidence",
    "EvidenceKind",
    "PurityChecker",
    "PurityResult",
    "PurityVerdict",
    "check_purity",
    "check_purity_transitive",
]
